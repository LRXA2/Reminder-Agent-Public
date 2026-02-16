from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import timezone

import dateparser
from telegram import Update

from src.app.handlers.reminder_formatting import format_due_display, format_reminder_brief
from src.clients.ollama_client import OllamaClient
from src.core.config import Settings
from src.storage.database import Database


@dataclass
class ReminderDraft:
    title: str
    notes: str
    priority: str
    due_at_utc: str


@dataclass
class PendingDraftBatch:
    source_kind: str
    user_id: int
    username: str | None
    drafts: list[ReminderDraft]


class ReminderDraftManager:
    def __init__(self, db: Database, ollama: OllamaClient, settings: Settings):
        self.db = db
        self.ollama = ollama
        self.settings = settings
        self.pending_by_chat: dict[int, PendingDraftBatch] = {}

    async def propose_from_text(self, update: Update, source_kind: str, content: str, user_instruction: str) -> bool:
        if not update.message or not update.effective_user:
            return False
        chat_id = update.effective_chat.id

        await update.message.reply_text("Analyzing content and drafting reminders...")
        try:
            payload = self._extract_drafts_from_content(content, user_instruction)
            if not payload.get("appropriate"):
                reason = (payload.get("reason") or "No reminder-worthy items were detected.").strip()
                await update.message.reply_text(f"Done. I found no reminders to suggest. {reason}")
                return True

            parsed_drafts = self._build_drafts(payload.get("reminders") or [], content)
            if not parsed_drafts:
                await update.message.reply_text(
                    "Done, but I could not extract valid reminder drafts yet. "
                    "You can still create one with /add or reply with exact details."
                )
                return True

            self.pending_by_chat[chat_id] = PendingDraftBatch(
                source_kind=source_kind,
                user_id=update.effective_user.id,
                username=update.effective_user.username,
                drafts=parsed_drafts,
            )
            await update.message.reply_text("Done. Please review the draft reminders below.")
            await update.message.reply_text(self._render_batch(chat_id))
            return True
        except Exception as exc:
            await update.message.reply_text(f"I hit an error while drafting reminders: {exc}")
            return True

    async def handle_followup(self, update: Update, text: str) -> bool:
        if not update.message:
            return False
        chat_id = update.effective_chat.id
        batch = self.pending_by_chat.get(chat_id)
        if not batch:
            return False

        lowered = text.strip().lower()
        confirm_aliases = {
            "add as reminder",
            "add as reminders",
            "save reminder",
            "save reminders",
            "create reminder",
            "create reminders",
        }
        if lowered in {"cancel", "skip", "discard", "no"}:
            self.pending_by_chat.pop(chat_id, None)
            await update.message.reply_text("Okay, discarded draft reminders.")
            return True

        if lowered in {"show", "list", "preview"}:
            await update.message.reply_text(self._render_batch(chat_id))
            return True

        if lowered == "yes" or lowered.startswith("confirm") or lowered in confirm_aliases:
            indices = self._parse_indices(lowered)
            selected = self._select_drafts(batch.drafts, indices)
            if selected is None:
                await update.message.reply_text("Invalid draft selection. Example: confirm 1,3")
                return True

            missing = [
                i + 1
                for i, draft in enumerate(selected)
                if not draft.title.strip() or not draft.due_at_utc or draft.priority not in {"immediate", "high", "mid", "low"}
            ]
            if missing:
                await update.message.reply_text(
                    "Some selected drafts are incomplete (title/priority/due). "
                    "Use: edit <n> title:<...> p:<...> at:<...>"
                )
                return True

            await update.message.reply_text("Saving selected reminders...")
            try:
                lines: list[str] = []
                for draft in selected:
                    app_user_id = self.db.upsert_user(batch.user_id, batch.username, self.settings.default_timezone)
                    reminder_id = self.db.create_reminder(
                        user_id=app_user_id,
                        source_message_id=None,
                        source_kind=batch.source_kind,
                        title=draft.title,
                        notes=draft.notes,
                        priority=draft.priority,
                        due_at_utc=draft.due_at_utc,
                        timezone_name=self.settings.default_timezone,
                        chat_id_to_notify=chat_id,
                        recurrence_rule=None,
                    )
                    lines.append(
                        format_reminder_brief(reminder_id, draft.title, draft.due_at_utc, self.settings.default_timezone)
                    )

                self.pending_by_chat.pop(chat_id, None)
                await update.message.reply_text("Done. Saved reminders:")
                await update.message.reply_text("\n\n".join(lines))
            except Exception as exc:
                await update.message.reply_text(f"I hit an error while saving reminders: {exc}")
            return True

        if lowered.startswith("remove "):
            indices = self._parse_indices(lowered)
            if not indices:
                await update.message.reply_text("Usage: remove <n> or remove 1,3")
                return True
            new_drafts = [draft for i, draft in enumerate(batch.drafts, start=1) if i not in set(indices)]
            if not new_drafts:
                self.pending_by_chat.pop(chat_id, None)
                await update.message.reply_text("All draft reminders removed.")
                return True
            batch.drafts = new_drafts
            await update.message.reply_text(self._render_batch(chat_id))
            return True

        if lowered.startswith("edit "):
            ok, message = self._apply_edit(chat_id, text)
            await update.message.reply_text(message)
            return True

        await update.message.reply_text(
            "I still have draft reminders waiting.\n"
            "Try one of these:\n"
            "- confirm\n"
            "- confirm 1,3\n"
            "- edit 2 title:... p:high at:tomorrow 9am\n"
            "- remove 2\n"
            "- show\n"
            "- cancel"
        )
        return True

    def _apply_edit(self, chat_id: int, text: str) -> tuple[bool, str]:
        batch = self.pending_by_chat.get(chat_id)
        if not batch:
            return False, "No pending drafts."

        match = re.match(r"edit\s+(\d+)\s+(.+)$", text.strip(), re.IGNORECASE)
        if not match:
            return False, "Usage: edit <n> title:<...> p:<...> at:<...> notes:<...>"

        idx = int(match.group(1))
        if idx < 1 or idx > len(batch.drafts):
            return False, "Draft index out of range."
        edits_text = match.group(2)
        draft = batch.drafts[idx - 1]

        title = self._extract_field(edits_text, "title")
        notes = self._extract_field(edits_text, "notes")
        priority_match = re.search(r"(?:p|priority)\s*:\s*(immediate|high|mid|low)\b", edits_text, re.IGNORECASE)
        at_match = re.search(r"at\s*:\s*(.+?)(?=\s+(?:title|notes|p|priority)\s*:|$)", edits_text, re.IGNORECASE)

        if title is not None:
            draft.title = title
        if notes is not None:
            draft.notes = notes
        if priority_match:
            draft.priority = priority_match.group(1).lower()
        if at_match:
            due = self._parse_due_to_utc(at_match.group(1).strip())
            if not due:
                return False, "Invalid at: value. Example: at:tomorrow 9am"
            draft.due_at_utc = due

        return True, self._render_batch(chat_id)

    def _extract_field(self, text: str, field_name: str) -> str | None:
        pattern = rf"{field_name}\s*:\s*(.+?)(?=\s+(?:title|notes|p|priority|at)\s*:|$)"
        match = re.search(pattern, text, re.IGNORECASE)
        if not match:
            return None
        return match.group(1).strip()

    def _parse_indices(self, text: str) -> list[int]:
        numbers = re.findall(r"\d+", text)
        return [int(n) for n in numbers] if numbers else []

    def _select_drafts(self, drafts: list[ReminderDraft], indices: list[int]) -> list[ReminderDraft] | None:
        if not indices:
            return drafts
        selected: list[ReminderDraft] = []
        for idx in indices:
            if idx < 1 or idx > len(drafts):
                return None
            selected.append(drafts[idx - 1])
        return selected

    def _render_batch(self, chat_id: int) -> str:
        batch = self.pending_by_chat[chat_id]
        lines = ["Proposed reminders (confirm before saving):"]
        for i, draft in enumerate(batch.drafts, start=1):
            lines.append(
                f"{i}) {draft.title} | {format_due_display(draft.due_at_utc, self.settings.default_timezone)} | {draft.priority}"
            )
        lines.append("")
        lines.append("What do you want to do?")
        lines.append("- Save all: confirm")
        lines.append("- Save selected: confirm 1,3")
        lines.append("- Edit one: edit 2 title:Submit concept p:high at:28 feb 11am")
        lines.append("- Remove one: remove 2")
        lines.append("- Show list again: show")
        lines.append("- Cancel all: cancel")
        return "\n".join(lines)

    def _extract_drafts_from_content(self, content: str, user_instruction: str) -> dict:
        prompt = (
            "You are a reminder planner. Determine if reminders are appropriate from the provided summary/content. "
            "Return STRICT JSON ONLY in this schema: "
            '{"appropriate": true|false, "reason": "...", "reminders": ['
            '{"title":"...","notes":"...","priority":"immediate|high|mid|low","due_text":"..."}]}. '
            "Rules: suggest 0-5 reminders max; title actionable 3-12 words; notes <= 280 chars; "
            "do not use generic titles like Summary; only include reminders with clear actionable tasks. "
            "If due date is unclear, set due_text to empty string."
            f"\nUser instruction: {user_instruction}\n\nContent:\n{content[:22000]}"
        )
        raw = self.ollama.generate_text(prompt)
        parsed = self._parse_json_object(raw)
        if not parsed:
            return {"appropriate": False, "reason": "Could not parse reminder suggestions.", "reminders": []}
        parsed.setdefault("appropriate", False)
        parsed.setdefault("reason", "")
        parsed.setdefault("reminders", [])
        return parsed

    def _build_drafts(self, reminders: list, fallback_notes: str) -> list[ReminderDraft]:
        drafts: list[ReminderDraft] = []
        for raw in reminders[:5]:
            if not isinstance(raw, dict):
                continue
            title = str(raw.get("title") or "").strip()
            if not title:
                continue
            notes = str(raw.get("notes") or "").strip()[:280]
            if not notes:
                notes = fallback_notes[:280]
            priority = str(raw.get("priority") or "mid").lower().strip()
            if priority not in {"immediate", "high", "mid", "low"}:
                priority = "mid"
            due_text = str(raw.get("due_text") or "").strip()
            due_at_utc = self._parse_due_to_utc(due_text) if due_text else ""
            drafts.append(ReminderDraft(title=title, notes=notes, priority=priority, due_at_utc=due_at_utc))
        return drafts

    def _parse_due_to_utc(self, due_text: str) -> str:
        if not due_text:
            return ""
        due_dt = dateparser.parse(
            due_text,
            settings={
                "TIMEZONE": self.settings.default_timezone,
                "RETURN_AS_TIMEZONE_AWARE": True,
                "PREFER_DATES_FROM": "future",
            },
        )
        if due_dt is None:
            return ""
        return due_dt.astimezone(timezone.utc).isoformat()

    def _parse_json_object(self, text: str) -> dict | None:
        try:
            loaded = json.loads(text)
            if isinstance(loaded, dict):
                return loaded
        except Exception:
            pass

        match = re.search(r"\{[\s\S]*\}", text)
        if not match:
            return None
        try:
            loaded = json.loads(match.group(0))
        except Exception:
            return None
        return loaded if isinstance(loaded, dict) else None
