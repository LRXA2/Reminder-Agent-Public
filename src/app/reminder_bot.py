from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone

import dateparser
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from dateparser.search import search_dates
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from src.clients.ollama_client import OllamaClient
from src.core.config import Settings
from src.storage.database import Database


LOGGER = logging.getLogger(__name__)

HELP_TEXT = """Reminder Bot Commands

/help
Show this help message

/add <task> [p:immediate|high|mid|low] [at:<time>] [every:daily|weekly|monthly]
Example: /add Pay rent p:high at:tomorrow 9am

/done <id>
Example: /done 12

/list all
/list priority <immediate|high|mid|low>
/list due <Nd>
Example: /list due 14d
/list today
/list tomorrow
/list overdue

/summary
Summarize recent monitored group messages

You can also type natural language like:
- summarize for me
- help me summarize
- summarize it for me and remind me tomorrow 9am p:high
- what hackathons are available on 1 Mar - 15 Mar

/models
List installed Ollama models

/model [name]
Show active model or set model for summaries

/status
Show Ollama and GPU status
"""


class ReminderBot:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.db = Database(settings.db_path)
        self.pending_summary_followups: dict[int, dict[str, str]] = {}
        self.ollama = OllamaClient(settings.ollama_base_url, settings.ollama_model)
        saved_model = self.db.get_app_setting("ollama_model")
        if saved_model:
            self.ollama.set_model(saved_model)
        elif settings.ollama_model:
            self.db.set_app_setting("ollama_model", settings.ollama_model)
        ollama_ready = self.ollama.ensure_server(
            autostart=settings.ollama_autostart,
            timeout_seconds=settings.ollama_start_timeout_seconds,
            use_highest_vram_gpu=settings.ollama_use_highest_vram_gpu,
        )
        if not ollama_ready:
            LOGGER.warning("Ollama is not reachable at %s", settings.ollama_base_url)
        self.scheduler = AsyncIOScheduler(timezone="UTC")
        self.app = Application.builder().token(settings.telegram_bot_token).build()
        self._register_handlers()
        self._register_jobs()

    def _register_handlers(self) -> None:
        self.app.add_handler(MessageHandler(filters.ALL, self._ingest_message), group=-1)
        self.app.add_handler(CommandHandler("help", self.help_command))
        self.app.add_handler(CommandHandler("add", self.add_command))
        self.app.add_handler(CommandHandler("done", self.done_command))
        self.app.add_handler(CommandHandler("list", self.list_command))
        self.app.add_handler(CommandHandler("summary", self.summary_command))
        self.app.add_handler(CommandHandler("models", self.models_command))
        self.app.add_handler(CommandHandler("model", self.model_command))
        self.app.add_handler(CommandHandler("status", self.status_command))
        self.app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self.normal_chat_handler))

    def _register_jobs(self) -> None:
        self.scheduler.add_job(self.process_due_reminders, "interval", seconds=30)
        self.scheduler.add_job(self.cleanup_archives, "cron", hour=1, minute=0)
        digest_times = self.settings.digest_times_utc or ((self.settings.digest_hour_utc, self.settings.digest_minute_utc),)
        seen: set[tuple[int, int]] = set()
        for hour, minute in digest_times:
            if (hour, minute) in seen:
                continue
            seen.add((hour, minute))
            self.scheduler.add_job(
                self.send_daily_digest,
                "cron",
                hour=hour,
                minute=minute,
            )

    async def _ingest_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not update.message:
            return
        message = update.message
        text = message.text or ""
        source_type = "group" if message.chat.type in {"group", "supergroup"} else "dm"
        received_at = message.date.astimezone(timezone.utc).isoformat()
        sender_id = message.from_user.id if message.from_user else None
        self.db.save_inbound_message(
            chat_id=message.chat_id,
            telegram_message_id=message.message_id,
            sender_telegram_id=sender_id,
            text=text,
            chat_type=message.chat.type,
            source_type=source_type,
            received_at_utc=received_at,
        )

    async def help_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if update.message:
            await update.message.reply_text(HELP_TEXT)

    async def add_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not update.message or not update.effective_user:
            return
        raw = " ".join(context.args).strip()
        if not raw:
            await update.message.reply_text("Usage: /add <task> p:high at:tomorrow 9am")
            return

        parsed = self._parse_add_payload(raw)
        if parsed.get("error"):
            await update.message.reply_text(parsed["error"])
            return

        timezone_name = self.settings.default_timezone
        user_id = self.db.upsert_user(update.effective_user.id, update.effective_user.username, timezone_name)

        reminder_id = self.db.create_reminder(
            user_id=user_id,
            source_message_id=None,
            source_kind="user_input",
            title=parsed["title"],
            notes="",
            priority=parsed["priority"],
            due_at_utc=parsed["due_at_utc"],
            timezone_name=timezone_name,
            chat_id_to_notify=update.effective_chat.id,
            recurrence_rule=parsed["recurrence"],
        )
        await update.message.reply_text(
            f"Saved reminder #{reminder_id}: {parsed['title']}\n"
            f"Priority: {parsed['priority']}\n"
            f"Due (UTC): {parsed['due_at_utc']}"
        )

    async def done_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not update.message:
            return
        if not context.args:
            await update.message.reply_text("Usage: /done <id>")
            return
        try:
            reminder_id = int(context.args[0])
        except ValueError:
            await update.message.reply_text("Reminder id must be a number.")
            return

        ok = self.db.mark_done_and_archive(reminder_id)
        if ok:
            await update.message.reply_text(f"Reminder #{reminder_id} archived.")
        else:
            await update.message.reply_text(f"Reminder #{reminder_id} not found or already archived.")

    async def list_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not update.message:
            return

        if not context.args:
            await update.message.reply_text("Usage: /list all | /list priority high | /list due 14d")
            return

        mode = context.args[0].lower()
        rows = []
        try:
            if mode == "all":
                rows = self.db.list_reminders("all")
            elif mode == "priority" and len(context.args) >= 2:
                rows = self.db.list_reminders("priority", context.args[1].lower())
            elif mode == "due" and len(context.args) >= 2:
                value = context.args[1].lower().strip()
                if not value.endswith("d"):
                    await update.message.reply_text("Use day format like: /list due 14d")
                    return
                rows = self.db.list_reminders("due_days", value[:-1])
            elif mode in {"today", "tomorrow", "overdue"}:
                rows = self.db.list_reminders(mode)
            else:
                await update.message.reply_text("Unknown list filter. Try /help")
                return
        except ValueError:
            await update.message.reply_text("Invalid list value. Try /help")
            return

        if not rows:
            await update.message.reply_text("No matching open reminders.")
            return

        lines = ["Open reminders:"]
        for row in rows[:50]:
            lines.append(
                f"#{row['id']} [{row['priority'].upper()}] {row['title']} - {row['due_at_utc']}"
            )
        await update.message.reply_text("\n".join(lines))

    async def summary_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not update.message:
            return

        if not self.settings.monitored_group_chat_id:
            await update.message.reply_text("MONITORED_GROUP_CHAT_ID is not set.")
            return

        summary = await self._build_group_summary()
        await update.message.reply_text(summary)

    async def models_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not update.message:
            return
        models = self.ollama.list_models()
        if not models:
            await update.message.reply_text("No Ollama models found. Pull one with: ollama pull <model>")
            return

        active = self.ollama.get_model()
        lines = ["Installed Ollama models:"]
        for model in models:
            marker = " (active)" if model == active else ""
            lines.append(f"- {model}{marker}")
        await update.message.reply_text("\n".join(lines))

    async def model_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not update.message:
            return

        if not context.args:
            active = self.ollama.get_model()
            if not active:
                await update.message.reply_text("No active model yet. Use /models and then /model <name>")
            else:
                await update.message.reply_text(f"Active model: {active}")
            return

        chosen = " ".join(context.args).strip()
        models = self.ollama.list_models()
        if chosen not in models:
            await update.message.reply_text(
                "Model not installed. Run: ollama pull "
                f"{chosen}"
            )
            return

        self.ollama.set_model(chosen)
        self.db.set_app_setting("ollama_model", chosen)
        await update.message.reply_text(f"Active model set to: {chosen}")

    async def status_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not update.message:
            return

        ollama_ready = self.ollama.ensure_server(
            autostart=False,
            timeout_seconds=2,
            use_highest_vram_gpu=False,
        )
        model = self.ollama.get_model() or "(none)"
        gpu = self.ollama.detect_nvidia_gpu()
        ps_output = self.ollama.ollama_ps()

        lines = [
            f"Ollama server: {'running' if ollama_ready else 'not reachable'}",
            f"Active model: {model}",
        ]

        if gpu.get("has_gpu"):
            lines.append("Nvidia GPU: " + ", ".join(gpu.get("gpus", [])))
        else:
            lines.append("Nvidia GPU: not detected")

        lines.append("ollama ps:")
        lines.append(ps_output)
        await update.message.reply_text("\n".join(lines))

    async def normal_chat_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not update.message or not update.effective_user:
            return

        chat_id = update.effective_chat.id
        text = (update.message.text or "").strip()
        if not text:
            return

        if chat_id != self.settings.personal_chat_id:
            return

        pending = self.pending_summary_followups.get(chat_id)
        if pending:
            await self._handle_summary_followup(update, text, pending)
            return

        lowered = text.lower()
        if self._has_summary_intent(lowered):
            await self._handle_summary_intent(update, text)
            return

        if self._has_hackathon_query_intent(lowered):
            await self._handle_hackathon_query(update, text)
            return

        if "remind me" not in lowered and not lowered.startswith("todo"):
            return

        parsed = self._parse_add_payload(text)
        if parsed.get("error"):
            await update.message.reply_text(
                "I detected reminder intent but need a date/time. Example: /add Pay rent at:tomorrow 9am"
            )
            return

        user_id = self.db.upsert_user(
            update.effective_user.id,
            update.effective_user.username,
            self.settings.default_timezone,
        )
        reminder_id = self.db.create_reminder(
            user_id=user_id,
            source_message_id=None,
            source_kind="user_input",
            title=parsed["title"],
            notes="",
            priority=parsed["priority"],
            due_at_utc=parsed["due_at_utc"],
            timezone_name=self.settings.default_timezone,
            chat_id_to_notify=chat_id,
            recurrence_rule=parsed["recurrence"],
        )
        await update.message.reply_text(f"Added reminder #{reminder_id}: {parsed['title']}")

    async def _handle_summary_intent(self, update: Update, text: str) -> None:
        if not update.message or not update.effective_user:
            return

        inline_content = self._extract_summary_content(text)
        if inline_content:
            summary = self._summarize_inline_text(inline_content)
        elif self.settings.monitored_group_chat_id:
            summary = await self._build_group_summary()
        else:
            await update.message.reply_text(
                "Please paste the content after your summarize request, or set MONITORED_GROUP_CHAT_ID for group summaries."
            )
            return

        await update.message.reply_text(summary)

        details = self._extract_due_and_priority(text)
        if details.get("due_at_utc") and details.get("priority"):
            user_id = self.db.upsert_user(
                update.effective_user.id,
                update.effective_user.username,
                self.settings.default_timezone,
            )
            reminder_id = self.db.create_reminder(
                user_id=user_id,
                source_message_id=None,
                source_kind="group_summary",
                title="Review summary action items",
                notes=summary[:1500],
                priority=details["priority"],
                due_at_utc=details["due_at_utc"],
                timezone_name=self.settings.default_timezone,
                chat_id_to_notify=update.effective_chat.id,
                recurrence_rule=None,
            )
            await update.message.reply_text(
                f"Saved reminder #{reminder_id} from summary with priority {details['priority']}"
            )
            return

        self.pending_summary_followups[update.effective_chat.id] = {
            "summary": summary[:1500],
            "title": "Review summary action items",
        }
        await update.message.reply_text(
            "I can turn this into a notification. Reply with urgency and due date/time. "
            "Example: high tomorrow 9am"
        )

    async def _handle_summary_followup(self, update: Update, text: str, pending: dict[str, str]) -> None:
        if not update.message or not update.effective_user:
            return

        lowered = text.strip().lower()
        if lowered in {"cancel", "skip", "never mind"}:
            self.pending_summary_followups.pop(update.effective_chat.id, None)
            await update.message.reply_text("Okay, cancelled summary reminder creation.")
            return

        details = self._extract_due_and_priority(text)
        missing_fields = []
        if not details.get("priority"):
            missing_fields.append("urgency (immediate/high/mid/low)")
        if not details.get("due_at_utc"):
            missing_fields.append("due date/time")
        if missing_fields:
            await update.message.reply_text(
                "I still need: " + ", ".join(missing_fields) + ". Example: high tomorrow 9am"
            )
            return

        user_id = self.db.upsert_user(
            update.effective_user.id,
            update.effective_user.username,
            self.settings.default_timezone,
        )
        reminder_id = self.db.create_reminder(
            user_id=user_id,
            source_message_id=None,
            source_kind="group_summary",
            title=pending.get("title", "Review summary action items"),
            notes=pending.get("summary", ""),
            priority=details["priority"],
            due_at_utc=details["due_at_utc"],
            timezone_name=self.settings.default_timezone,
            chat_id_to_notify=update.effective_chat.id,
            recurrence_rule=None,
        )
        self.pending_summary_followups.pop(update.effective_chat.id, None)
        await update.message.reply_text(f"Saved reminder #{reminder_id} from summary.")

    def _has_summary_intent(self, lowered_text: str) -> bool:
        patterns = [
            "summarize for me",
            "help me summarize",
            "summarize it for me",
            "can you summarize",
        ]
        return any(p in lowered_text for p in patterns)

    def _has_hackathon_query_intent(self, lowered_text: str) -> bool:
        if "hackathon" not in lowered_text and "hackathons" not in lowered_text:
            return False
        query_markers = ["what", "which", "available", "list", "show", "between", "from"]
        return any(marker in lowered_text for marker in query_markers)

    def _extract_summary_content(self, text: str) -> str:
        markers = [
            "summarize for me",
            "help me summarize",
            "summarize it for me",
            "can you summarize",
        ]
        lowered = text.lower()
        start = -1
        marker_used = ""
        for marker in markers:
            idx = lowered.find(marker)
            if idx >= 0:
                start = idx
                marker_used = marker
                break
        if start < 0:
            return ""
        content = text[start + len(marker_used) :].strip()
        return content

    def _summarize_inline_text(self, content: str) -> str:
        cleaned_lines = []
        for raw_line in content.splitlines():
            line = raw_line.strip()
            if not line:
                continue
            if len(line) > 700:
                line = line[:700] + "..."
            cleaned_lines.append(line)
        if not cleaned_lines:
            return "I did not find enough text to summarize."
        return self.ollama.summarize_messages(cleaned_lines)

    async def _handle_hackathon_query(self, update: Update, user_query: str) -> None:
        if not update.message:
            return
        rows = self.db.fetch_recent_chat_messages(update.effective_chat.id, limit=300)
        if not rows:
            await update.message.reply_text("I do not have message history yet. Paste or forward hackathon posts first.")
            return

        corpus_lines: list[str] = []
        for row in reversed(rows):
            text = (row["text"] or "").strip()
            if not text:
                continue
            if len(text) > 1000:
                text = text[:1000] + "..."
            corpus_lines.append(f"[{row['received_at_utc']}] {text}")

        if not corpus_lines:
            await update.message.reply_text("I do not have enough text content to answer that yet.")
            return

        prompt = (
            "You are an assistant that extracts hackathon opportunities from chat history. "
            "Use ONLY the content provided. If date range is requested, filter accordingly. "
            "If unknown, say unknown. Return concise bullet points with: Name, Date/Time, Location, Link (if any).\n\n"
            f"User question:\n{user_query}\n\n"
            "Chat history:\n"
            + "\n".join(corpus_lines[-180:])
        )
        answer = self.ollama.generate_text(prompt)
        await update.message.reply_text(answer)

    def _extract_due_and_priority(self, text: str) -> dict[str, str]:
        priority_match = re.search(r"(?:p|priority)?\s*:?\s*(immediate|high|mid|low)\b", text, re.IGNORECASE)
        priority = priority_match.group(1).lower() if priority_match else ""

        due_dt = None
        found = search_dates(
            text,
            settings={
                "TIMEZONE": self.settings.default_timezone,
                "RETURN_AS_TIMEZONE_AWARE": True,
                "PREFER_DATES_FROM": "future",
            },
        )
        if found:
            due_dt = found[-1][1]

        due_utc = due_dt.astimezone(timezone.utc).isoformat() if due_dt else ""
        return {
            "priority": priority,
            "due_at_utc": due_utc,
        }

    async def process_due_reminders(self) -> None:
        now_iso = datetime.now(timezone.utc).isoformat()
        rows = self.db.get_due_reminders(now_iso)
        for row in rows:
            chat_id = int(row["chat_id_to_notify"])
            try:
                await self.app.bot.send_message(
                    chat_id=chat_id,
                    text=f"🔔 Reminder #{row['id']}: {row['title']} ({row['priority']})",
                )
            except Exception as exc:
                LOGGER.exception("Failed to send reminder %s: %s", row["id"], exc)
                continue
            self.db.mark_reminder_notified(int(row["id"]), row["due_at_utc"])

            recurrence = (row["recurrence_rule"] or "").strip().lower()
            if recurrence:
                next_due = self._compute_next_due(row["due_at_utc"], recurrence)
                if next_due:
                    self.db.update_recurring_due(int(row["id"]), next_due)

    async def cleanup_archives(self) -> None:
        deleted = self.db.delete_old_archived(self.settings.archive_retention_days)
        if deleted:
            LOGGER.info("Deleted %s archived reminders older than retention", deleted)

    async def send_daily_digest(self) -> None:
        if not self.settings.personal_chat_id:
            return
        lines = ["Daily digest"]
        tomorrow_items = self.db.list_reminders("due_days", "1")
        if tomorrow_items:
            lines.append("Tomorrow / next 24h reminders:")
            for row in tomorrow_items[:10]:
                lines.append(f"- #{row['id']} [{row['priority']}] {row['title']} @ {row['due_at_utc']}")

        if self.settings.monitored_group_chat_id:
            summary = await self._build_group_summary(save=False)
            lines.append("Group summary:")
            lines.append(summary)

        await self.app.bot.send_message(chat_id=self.settings.personal_chat_id, text="\n".join(lines))

    async def _build_group_summary(self, save: bool = True) -> str:
        rows = self.db.fetch_recent_group_messages(self.settings.monitored_group_chat_id, limit=50)
        if not rows:
            return "No recent messages in monitored group."

        lines = []
        for row in reversed(rows):
            text = (row["text"] or "").strip()
            if not text:
                continue
            if len(text) > 500:
                text = text[:500] + "..."
            lines.append(f"[{row['received_at_utc']}] {text}")

        summary = self.ollama.summarize_messages(lines)
        if save:
            now = datetime.now(timezone.utc)
            window_start = (now - timedelta(hours=24)).isoformat()
            self.db.save_summary(
                self.settings.monitored_group_chat_id,
                window_start,
                now.isoformat(),
                summary,
            )
        return summary

    def _parse_add_payload(self, payload: str) -> dict[str, str]:
        text = payload.strip()

        priority_match = re.search(r"(?:p|priority)\s*:\s*(immediate|high|mid|low)\b", text, re.IGNORECASE)
        priority = priority_match.group(1).lower() if priority_match else "mid"
        if priority_match:
            text = text[: priority_match.start()] + text[priority_match.end() :]

        recur_match = re.search(r"every\s*:\s*(daily|weekly|monthly)\b", text, re.IGNORECASE)
        recurrence = recur_match.group(1).lower() if recur_match else ""
        if recur_match:
            text = text[: recur_match.start()] + text[recur_match.end() :]

        due_dt = None
        at_match = re.search(r"at\s*:\s*(.+)$", text, re.IGNORECASE)
        if at_match:
            dt_text = at_match.group(1).strip()
            due_dt = dateparser.parse(
                dt_text,
                settings={
                    "TIMEZONE": self.settings.default_timezone,
                    "RETURN_AS_TIMEZONE_AWARE": True,
                    "PREFER_DATES_FROM": "future",
                },
            )
            text = text[: at_match.start()].strip()
        else:
            found = search_dates(
                text,
                settings={
                    "TIMEZONE": self.settings.default_timezone,
                    "RETURN_AS_TIMEZONE_AWARE": True,
                    "PREFER_DATES_FROM": "future",
                },
            )
            if found:
                dt_phrase, dt_value = found[-1]
                due_dt = dt_value
                text = text.replace(dt_phrase, " ").strip()

        cleaned = re.sub(r"\s+", " ", text).strip(" -")
        cleaned = re.sub(r"^(remind me to|remind me|todo)\s+", "", cleaned, flags=re.IGNORECASE).strip()

        if not cleaned:
            return {"error": "Missing reminder title. Example: /add Pay rent at:tomorrow 9am"}

        if due_dt is None:
            return {"error": "Missing or invalid date/time. Example: /add Pay rent at:tomorrow 9am"}

        due_utc = due_dt.astimezone(timezone.utc).isoformat()
        return {
            "title": cleaned,
            "priority": priority,
            "due_at_utc": due_utc,
            "recurrence": recurrence,
        }

    def _compute_next_due(self, due_at_utc: str, recurrence: str) -> str | None:
        try:
            current = datetime.fromisoformat(due_at_utc)
        except ValueError:
            return None
        if current.tzinfo is None:
            current = current.replace(tzinfo=timezone.utc)

        if recurrence == "daily":
            nxt = current + timedelta(days=1)
        elif recurrence == "weekly":
            nxt = current + timedelta(days=7)
        elif recurrence == "monthly":
            nxt = current + timedelta(days=30)
        else:
            return None
        return nxt.astimezone(timezone.utc).isoformat()

    def run_polling(self) -> None:
        logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s - %(message)s")
        logging.getLogger("httpx").setLevel(logging.WARNING)
        logging.getLogger("httpcore").setLevel(logging.WARNING)
        logging.getLogger("telegram").setLevel(logging.WARNING)
        self.scheduler.start()
        self.app.run_polling(drop_pending_updates=True)
