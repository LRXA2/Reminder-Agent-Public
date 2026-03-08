from __future__ import annotations

import json
import logging
import os
import re
import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from src.app.prompts import email_importance_prompt, email_summary_prompt
from src.app.handlers.services.gmail.light_filter import GmailLightFilter
from src.integrations.gmail_service import GmailService, ParsedEmail


LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class GmailAccountConfig:
    account_id: str
    credentials_file: str
    token_file: str
    query: str
    sender_allowlist: tuple[str, ...]
    sender_vip: tuple[str, ...]
    sender_system: tuple[str, ...]
    sender_trusted_domains: tuple[str, ...]
    sender_blocklist: tuple[str, ...]
    keyword_include: tuple[str, ...]
    keyword_exclude: tuple[str, ...]
    attachment_keyword_include: tuple[str, ...]
    filter_keys_file: str
    risky_tlds: tuple[str, ...]
    shortener_domains: tuple[str, ...]
    suspicious_phrases: tuple[str, ...]
    promotional_phrases: tuple[str, ...]
    urgent_subject_phrases: tuple[str, ...]
    telegram_chat_id: int


class GmailIngestHandler:
    DELIVERY_MODES = {"immediate", "batch", "hybrid"}
    MIN_CONTENT_CHARS = 40
    MIN_CONTENT_WORDS = 6

    def __init__(self, bot) -> None:
        self.bot = bot
        self._accounts = self._parse_accounts(self._load_accounts_json())
        self._services: dict[str, GmailService] = {}
        self._poll_lock = asyncio.Lock()
        self._light_filter = GmailLightFilter()

    def account_ids(self) -> list[str]:
        return [account.account_id for account in self._accounts]

    def is_enabled(self) -> bool:
        return bool(self.bot.settings.gmail_enabled and self._accounts)

    def account_count(self) -> int:
        return len(self._accounts)

    async def poll_all_accounts(self, force_batch_flush: bool = False) -> dict[str, int]:
        async with self._poll_lock:
            stats = {
                "accounts": len(self._accounts),
                "processed": 0,
                "important": 0,
                "notified": 0,
                "errors": 0,
            }
            if not self.bot.settings.gmail_enabled:
                return stats

            for account in self._accounts:
                try:
                    result = await self.poll_account(account)
                    stats["processed"] += result["processed"]
                    stats["important"] += result["important"]
                    stats["notified"] += result["notified"]
                    stats["errors"] += result["errors"]
                    stats["notified"] += await self._flush_account_batch(account, force=force_batch_flush)
                except Exception:
                    stats["errors"] += 1
                    LOGGER.exception("gmail poll account failed account=%s", account.account_id)
            return stats

    async def poll_account(self, account: GmailAccountConfig) -> dict[str, int]:
        stats = {"processed": 0, "important": 0, "notified": 0, "errors": 0}
        service = self._service_for(account)
        if not service.is_ready():
            self.bot.db.upsert_gmail_account_state(
                account_id=account.account_id,
                last_checked_at_utc=datetime.now(timezone.utc).isoformat(),
                last_error=service.get_last_error() or "gmail service unavailable",
            )
            stats["errors"] += 1
            return stats

        query = self._combined_query(account)
        message_ids = service.list_message_ids(query=query, max_results=50)
        for message_id in message_ids:
            if self.bot.db.is_gmail_message_processed(account.account_id, message_id):
                continue
            message = service.get_message(message_id)
            if message is None:
                stats["errors"] += 1
                continue
            parsed = service.extract_email_payload(message)
            if self._is_thread_cooldown_active(account.account_id, parsed.thread_id):
                self.bot.db.save_gmail_processed_message(
                    account_id=account.account_id,
                    gmail_message_id=parsed.gmail_message_id,
                    thread_id=parsed.thread_id,
                    from_email=parsed.from_email,
                    subject=parsed.subject,
                    snippet=parsed.snippet,
                    internal_date_utc=parsed.internal_date_utc,
                    label_ids=parsed.label_ids,
                    importance_score=0.0,
                    importance_reason="thread cooldown active",
                    is_important=False,
                    summary_text="",
                    notified=False,
                )
                stats["processed"] += 1
                continue
            if not self._has_minimum_content(parsed):
                self.bot.db.save_gmail_processed_message(
                    account_id=account.account_id,
                    gmail_message_id=parsed.gmail_message_id,
                    thread_id=parsed.thread_id,
                    from_email=parsed.from_email,
                    subject=parsed.subject,
                    snippet=parsed.snippet,
                    internal_date_utc=parsed.internal_date_utc,
                    label_ids=parsed.label_ids,
                    importance_score=0.0,
                    importance_reason="minimum content guard",
                    is_important=False,
                    summary_text="",
                    notified=False,
                )
                stats["processed"] += 1
                continue
            important, score, reason = await self._classify_importance(account, parsed)
            summary_text = ""
            notified = False
            if important and self.bot.settings.gmail_summary_enabled:
                summary_text = await self._summarize_email(account, parsed)
                if self._should_send_immediately(score):
                    notified = await self._notify_summary(account, parsed, summary_text, score, reason)
            self.bot.db.save_gmail_processed_message(
                account_id=account.account_id,
                gmail_message_id=parsed.gmail_message_id,
                thread_id=parsed.thread_id,
                from_email=parsed.from_email,
                subject=parsed.subject,
                snippet=parsed.snippet,
                internal_date_utc=parsed.internal_date_utc,
                label_ids=parsed.label_ids,
                importance_score=score,
                importance_reason=reason,
                is_important=important,
                summary_text=summary_text,
                notified=notified,
            )

            stats["processed"] += 1
            if important:
                stats["important"] += 1
            if notified:
                stats["notified"] += 1

        self.bot.db.upsert_gmail_account_state(
            account_id=account.account_id,
            last_checked_at_utc=datetime.now(timezone.utc).isoformat(),
            last_error="",
        )
        return stats

    async def _flush_account_batch(self, account: GmailAccountConfig, force: bool = False) -> int:
        mode = self._delivery_mode()
        if mode == "immediate":
            return 0
        if not self.bot.settings.gmail_summary_enabled:
            return 0

        now = datetime.now(timezone.utc)
        state_key = f"gmail_batch_last_sent_{account.account_id}"
        last_sent_raw = (self.bot.db.get_app_setting(state_key) or "").strip()
        if not force and last_sent_raw:
            try:
                last_sent = datetime.fromisoformat(last_sent_raw)
                if last_sent.tzinfo is None:
                    last_sent = last_sent.replace(tzinfo=timezone.utc)
                elapsed_seconds = (now - last_sent.astimezone(timezone.utc)).total_seconds()
                if elapsed_seconds < self.bot.settings.gmail_batch_interval_minutes * 60:
                    return 0
            except ValueError:
                pass

        rows = self.bot.db.list_unnotified_important_gmail_events(account.account_id, limit=20)
        if not rows:
            self.bot.db.set_app_setting(state_key, now.isoformat())
            return 0

        chat_id = account.telegram_chat_id or int(self.bot.settings.personal_chat_id)
        if not chat_id:
            return 0

        lines: list[str] = [f"[Gmail:{account.account_id}] Important email batch ({len(rows)})", ""]
        for idx, row in enumerate(rows, start=1):
            subject = str(row["subject"] or "").strip() or "(no subject)"
            sender = str(row["from_email"] or "").strip() or "(unknown sender)"
            score_text = f"{float(row['importance_score'] or 0.0):.2f}"
            reason = str(row["importance_reason"] or "").strip()
            summary = str(row["summary_text"] or "").strip() or str(row["snippet"] or "").strip() or "(no summary)"
            if len(summary) > 420:
                summary = summary[:420].rstrip() + "..."
            lines.append(f"{idx}) {subject}")
            lines.append(f"From: {sender}")
            lines.append(f"Importance: {score_text}" + (f" ({reason})" if reason else ""))
            lines.append(summary)
            lines.append("")

        body = "\n".join(lines).strip()[:3900]
        try:
            await self.bot.app.bot.send_message(chat_id=chat_id, text=body)
        except Exception as exc:
            LOGGER.warning("gmail batch notify failed account=%s error=%s", account.account_id, exc)
            return 0

        for row in rows:
            self.bot.db.mark_gmail_notified(account.account_id, str(row["gmail_message_id"]))
        self.bot.db.set_app_setting(state_key, now.isoformat())
        return len(rows)

    def _delivery_mode(self) -> str:
        mode = (self.bot.settings.gmail_delivery_mode or "batch").strip().lower()
        if mode not in self.DELIVERY_MODES:
            return "batch"
        return mode

    def _should_send_immediately(self, score: float) -> bool:
        mode = self._delivery_mode()
        if mode == "immediate":
            return True
        if mode == "batch":
            return False
        return score >= float(self.bot.settings.gmail_urgent_score_threshold)

    def _combined_query(self, account: GmailAccountConfig) -> str:
        global_query = (self.bot.settings.gmail_global_query or "").strip()
        account_query = (account.query or "").strip()
        if global_query and account_query:
            return f"({global_query}) ({account_query})"
        return account_query or global_query

    async def _classify_importance(self, account: GmailAccountConfig, parsed: ParsedEmail) -> tuple[bool, float, str]:
        rules_important, rules_score, rules_reason = self._classify_by_rules(account, parsed)
        if rules_important:
            return True, rules_score, rules_reason
        if self.bot.settings.gmail_require_rule_match:
            return False, rules_score, "rule gate: not matched"
        if not self.bot.settings.gmail_use_llm_importance:
            return False, rules_score, rules_reason
        llm_result = await self._classify_by_llm(account, parsed)
        if llm_result is None:
            return False, rules_score, rules_reason
        important, llm_score, llm_reason = llm_result
        return important, llm_score, llm_reason

    def _classify_by_rules(self, account: GmailAccountConfig, parsed: ParsedEmail) -> tuple[bool, float, str]:
        result = self._light_filter.classify(account, parsed, self.bot.settings)
        return result.important, result.score, result.reason

    async def _classify_by_llm(self, account: GmailAccountConfig, parsed: ParsedEmail) -> tuple[bool, float, str] | None:
        attachment_context = ""
        if parsed.attachment_names:
            attachment_context = "\nAttachments: " + ", ".join(parsed.attachment_names[:10])
        prompt = email_importance_prompt(
            account_id=account.account_id,
            sender=parsed.from_email,
            subject=parsed.subject,
            snippet=parsed.snippet,
            body_excerpt=(parsed.body_text[:8000] + attachment_context).strip(),
        )
        raw = await self.bot.run_gpu_task(self.bot.ollama.generate_text, prompt)
        data = self._parse_json_object(raw)
        if data is None:
            return None

        important = bool(data.get("important", False))
        score_raw = data.get("score", 0.0)
        try:
            score = max(0.0, min(1.0, float(score_raw)))
        except (TypeError, ValueError):
            score = 0.0
        reason = str(data.get("reason") or "llm classified as not important").strip()
        return important, score, reason

    async def _summarize_email(self, account: GmailAccountConfig, parsed: ParsedEmail) -> str:
        attachment_context = ""
        if parsed.attachment_names:
            attachment_context = "\nAttachments: " + ", ".join(parsed.attachment_names[:10])
        prompt = email_summary_prompt(
            account_id=account.account_id,
            sender=parsed.from_email,
            subject=parsed.subject,
            body_excerpt=(parsed.body_text[:18000] + attachment_context).strip(),
        )
        summary = (await self.bot.run_gpu_task(self.bot.ollama.generate_text, prompt) or "").strip()
        if not summary or summary.lower().startswith("summary unavailable") or "ollama error" in summary.lower():
            summary = self._build_fallback_summary(parsed)

        domains = self._extract_link_domains(parsed.links)
        metadata_lines: list[str] = []
        if domains:
            metadata_lines.append("Domains: " + ", ".join(domains[:4]))
        if parsed.links:
            metadata_lines.append("Links: " + ", ".join(parsed.links[:3]))
        if not metadata_lines:
            return summary
        return "\n".join(metadata_lines + ["", summary]).strip()

    async def _notify_summary(self, account: GmailAccountConfig, parsed: ParsedEmail, summary: str, score: float, reason: str) -> bool:
        chat_id = account.telegram_chat_id or int(self.bot.settings.personal_chat_id)
        if not chat_id:
            return False
        lines = [
            f"[Gmail:{account.account_id}] Important email",
            f"From: {parsed.from_email or '(unknown)'}",
            f"Subject: {parsed.subject or '(no subject)'}",
            f"Importance: {score:.2f} ({reason})",
        ]
        if parsed.attachment_names:
            lines.append("Attachments: " + ", ".join(parsed.attachment_names[:5]))
        lines.append("")
        lines.append(summary or parsed.snippet or "(no summary)")
        try:
            await self.bot.app.bot.send_message(chat_id=chat_id, text="\n".join(lines)[:3900])
            return True
        except Exception as exc:
            LOGGER.warning(
                "gmail telegram notify failed account=%s message=%s error=%s",
                account.account_id,
                parsed.gmail_message_id,
                exc,
            )
            return False

    def _service_for(self, account: GmailAccountConfig) -> GmailService:
        existing = self._services.get(account.account_id)
        if existing is not None:
            return existing
        service = GmailService(
            account_id=account.account_id,
            credentials_file=account.credentials_file,
            token_file=account.token_file,
        )
        self._services[account.account_id] = service
        return service

    def _parse_accounts(self, raw_json: str) -> list[GmailAccountConfig]:
        if not raw_json.strip():
            return []
        try:
            loaded = json.loads(raw_json)
        except json.JSONDecodeError as exc:
            LOGGER.warning("Invalid GMAIL_ACCOUNTS_JSON: %s", exc)
            return []

        if not isinstance(loaded, list):
            LOGGER.warning("GMAIL_ACCOUNTS_JSON must be a JSON array")
            return []

        accounts: list[GmailAccountConfig] = []
        for item in loaded:
            if not isinstance(item, dict):
                continue
            account_id = str(item.get("account_id") or "").strip()
            credentials_file = str(item.get("credentials_file") or "").strip()
            token_file = str(item.get("token_file") or "").strip()
            if not account_id or not credentials_file or not token_file:
                continue
            query = str(item.get("query") or "").strip()
            sender_allowlist = self._coerce_text_tuple(item.get("sender_allowlist"))
            sender_vip = self._coerce_text_tuple(item.get("sender_vip"))
            sender_system = self._coerce_text_tuple(item.get("sender_system"))
            sender_trusted_domains = self._coerce_text_tuple(item.get("sender_trusted_domains"))
            sender_blocklist = self._coerce_text_tuple(item.get("sender_blocklist"))
            keyword_include = self._coerce_text_tuple(item.get("keyword_include"))
            keyword_exclude = self._coerce_text_tuple(item.get("keyword_exclude"))
            attachment_keyword_include = self._coerce_text_tuple(item.get("attachment_keyword_include"))
            filter_keys_file = str(item.get("filter_keys_file") or "").strip()
            risky_tlds = self._coerce_text_tuple(item.get("risky_tlds"))
            shortener_domains = self._coerce_text_tuple(item.get("shortener_domains"))
            suspicious_phrases = self._coerce_text_tuple(item.get("suspicious_phrases"))
            promotional_phrases = self._coerce_text_tuple(item.get("promotional_phrases"))
            urgent_subject_phrases = self._coerce_text_tuple(item.get("urgent_subject_phrases"))
            overrides = self._load_filter_key_overrides(filter_keys_file)
            if "risky_tlds" in overrides:
                risky_tlds = overrides["risky_tlds"]
            if "shortener_domains" in overrides:
                shortener_domains = overrides["shortener_domains"]
            if "suspicious_phrases" in overrides:
                suspicious_phrases = overrides["suspicious_phrases"]
            if "promotional_phrases" in overrides:
                promotional_phrases = overrides["promotional_phrases"]
            if "urgent_subject_phrases" in overrides:
                urgent_subject_phrases = overrides["urgent_subject_phrases"]
            telegram_chat_id = self._coerce_int(item.get("telegram_chat_id"), 0)
            accounts.append(
                GmailAccountConfig(
                    account_id=account_id,
                    credentials_file=credentials_file,
                    token_file=token_file,
                    query=query,
                    sender_allowlist=sender_allowlist,
                    sender_vip=sender_vip,
                    sender_system=sender_system,
                    sender_trusted_domains=sender_trusted_domains,
                    sender_blocklist=sender_blocklist,
                    keyword_include=keyword_include,
                    keyword_exclude=keyword_exclude,
                    attachment_keyword_include=attachment_keyword_include,
                    filter_keys_file=filter_keys_file,
                    risky_tlds=risky_tlds,
                    shortener_domains=shortener_domains,
                    suspicious_phrases=suspicious_phrases,
                    promotional_phrases=promotional_phrases,
                    urgent_subject_phrases=urgent_subject_phrases,
                    telegram_chat_id=telegram_chat_id,
                )
            )
        return accounts

    def _load_accounts_json(self) -> str:
        accounts_file_raw = str(getattr(self.bot.settings, "gmail_accounts_file", "") or "").strip()
        if accounts_file_raw:
            file_path = self._resolve_accounts_file_path(accounts_file_raw)
            if file_path is None:
                LOGGER.warning("GMAIL_ACCOUNTS_FILE not found: %s", accounts_file_raw)
                return "[]"
            try:
                text = file_path.read_text(encoding="utf-8").strip()
            except OSError as exc:
                LOGGER.warning("Failed reading GMAIL_ACCOUNTS_FILE=%s error=%s", file_path, exc)
                return "[]"
            return text or "[]"
        return self.bot.settings.gmail_accounts_json

    def _resolve_accounts_file_path(self, raw_path: str) -> Path | None:
        candidate = Path(raw_path)
        if candidate.is_absolute():
            return candidate if candidate.exists() else None

        project_root = Path(__file__).resolve().parents[4]
        env_file = os.getenv("ENV_FILE", "").strip()
        search_roots = [
            Path.cwd(),
            project_root,
            project_root.parent,
        ]
        if env_file:
            env_parent = Path(env_file).expanduser().resolve().parent
            search_roots.insert(0, env_parent)

        for root in search_roots:
            full = (root / candidate).resolve()
            if full.exists():
                return full
        return None

    def _load_filter_key_overrides(self, raw_path: str) -> dict[str, tuple[str, ...]]:
        if not raw_path:
            return {}
        file_path = self._resolve_accounts_file_path(raw_path)
        if file_path is None:
            LOGGER.warning("filter_keys_file not found: %s", raw_path)
            return {}
        try:
            loaded = json.loads(file_path.read_text(encoding="utf-8"))
        except Exception as exc:
            LOGGER.warning("failed reading filter_keys_file=%s error=%s", file_path, exc)
            return {}
        if not isinstance(loaded, dict):
            LOGGER.warning("filter_keys_file must be a JSON object: %s", file_path)
            return {}

        allowed = {
            "risky_tlds",
            "shortener_domains",
            "suspicious_phrases",
            "promotional_phrases",
            "urgent_subject_phrases",
        }
        overrides: dict[str, tuple[str, ...]] = {}
        for key in allowed:
            if key in loaded:
                overrides[key] = self._coerce_text_tuple(loaded.get(key))
        return overrides

    def _coerce_text_tuple(self, value: Any) -> tuple[str, ...]:
        if not isinstance(value, list):
            return tuple()
        cleaned: list[str] = []
        for item in value:
            text = str(item or "").strip().lower()
            if text:
                cleaned.append(text)
        return tuple(cleaned)

    def _coerce_int(self, value: Any, default: int) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return default

    def _is_thread_cooldown_active(self, account_id: str, thread_id: str) -> bool:
        cooldown_minutes = int(self.bot.settings.gmail_thread_cooldown_minutes)
        if cooldown_minutes <= 0 or not thread_id.strip():
            return False
        since_utc_iso = (datetime.now(timezone.utc) - timedelta(minutes=cooldown_minutes)).isoformat()
        return self.bot.db.has_recent_notified_gmail_thread(account_id, thread_id, since_utc_iso)

    def _has_minimum_content(self, parsed: ParsedEmail) -> bool:
        if parsed.attachment_names and (parsed.subject or parsed.snippet):
            return True
        source = (parsed.body_text or parsed.snippet or "").strip()
        if len(source) < self.MIN_CONTENT_CHARS:
            return False
        words = [token for token in re.split(r"\s+", source) if token]
        return len(words) >= self.MIN_CONTENT_WORDS

    def _build_fallback_summary(self, parsed: ParsedEmail) -> str:
        preview = (parsed.body_text or parsed.snippet or "").strip()
        if len(preview) > 500:
            preview = preview[:500].rstrip() + "..."
        lines = [
            "Why it matters: New email received from a monitored sender or matching rule.",
            "Action needed: Review this email.",
            "Deadlines: (none)",
            "Links: " + (", ".join(parsed.links[:3]) if parsed.links else "(none)"),
        ]
        if preview:
            lines.extend(["", preview])
        return "\n".join(lines)

    def _extract_link_domains(self, links: list[str]) -> list[str]:
        domains: list[str] = []
        for link in links:
            try:
                netloc = urlsplit(link).netloc.lower().strip()
            except Exception:
                continue
            if not netloc:
                continue
            if netloc.startswith("www."):
                netloc = netloc[4:]
            if netloc not in domains:
                domains.append(netloc)
        return domains

    def _parse_json_object(self, text: str) -> dict[str, Any] | None:
        if not text:
            return None
        try:
            loaded = json.loads(text)
            if isinstance(loaded, dict):
                return loaded
        except json.JSONDecodeError:
            pass

        match = re.search(r"\{[\s\S]*\}", text)
        if not match:
            return None
        try:
            loaded = json.loads(match.group(0))
        except json.JSONDecodeError:
            return None
        if isinstance(loaded, dict):
            return loaded
        return None
