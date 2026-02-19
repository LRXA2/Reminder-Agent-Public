from __future__ import annotations

import re
from collections.abc import Awaitable, Callable

from telegram import Update

from src.app.handlers.intent_parsing import (
    extract_due_and_priority,
    extract_summary_content,
    has_edit_intent,
    has_hackathon_query_intent,
    has_reminder_intent,
    has_summary_intent,
)
from src.app.handlers.operation_status import OperationStatus
from src.app.handlers.reminder_draft_manager import ReminderDraftManager
from src.app.handlers.reminder_formatting import format_reminder_brief
from src.app.messages import msg
from src.app.prompts import hackathon_query_prompt
from src.clients.ollama_client import OllamaClient
from src.core.config import Settings
from src.storage.database import Database


class TextInputHandler:
    def __init__(
        self,
        db: Database,
        ollama: OllamaClient,
        settings: Settings,
        draft_manager: ReminderDraftManager,
        run_gpu_task,
    ):
        self.db = db
        self.ollama = ollama
        self.settings = settings
        self.draft_manager = draft_manager
        self.run_gpu_task = run_gpu_task

    async def handle_message(
        self,
        update: Update,
        parse_add_payload: Callable[[str], dict[str, str]],
        build_group_summary: Callable[..., Awaitable[str]],
    ) -> bool:
        if not update.message or not update.effective_user:
            return False

        chat_id = update.effective_chat.id
        text = (update.message.text or "").strip()
        if not text:
            return False
        if chat_id != self.settings.personal_chat_id:
            return False

        lowered = text.lower()
        if update.message.reply_to_message and has_edit_intent(lowered):
            handled_edit = await self._handle_reply_edit(update, text)
            if handled_edit:
                return True

        if has_summary_intent(lowered):
            await self._handle_summary_intent(update, text, build_group_summary)
            return True

        if has_hackathon_query_intent(lowered):
            await self._handle_hackathon_query(update, text)
            return True

        if not has_reminder_intent(lowered):
            return False

        if update.message.reply_to_message:
            handled_reply = await self._handle_reply_reminder(update, text)
            if handled_reply:
                return True

        parsed = parse_add_payload(text)
        if parsed.get("error"):
            await update.message.reply_text(
                "I detected reminder intent but need a date/time. Example: /add Pay rent at:tomorrow 9am"
            )
            return True

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
            link=parsed.get("link", ""),
            priority=parsed["priority"],
            due_at_utc=parsed["due_at_utc"],
            timezone_name=self.settings.default_timezone,
            chat_id_to_notify=chat_id,
            recurrence_rule=parsed["recurrence"],
        )
        await update.message.reply_text(
            format_reminder_brief(reminder_id, parsed["title"], parsed["due_at_utc"], self.settings.default_timezone)
        )
        return True

    async def _handle_summary_intent(
        self,
        update: Update,
        text: str,
        build_group_summary: Callable[..., Awaitable[str]],
    ) -> None:
        if not update.message or not update.effective_user:
            return

        await OperationStatus.started(update, "Working on that summary now...")
        try:
            inline_content = extract_summary_content(text)
            if inline_content:
                summary = await self.run_gpu_task(self._summarize_inline_text, inline_content)
            elif self.settings.monitored_group_chat_id:
                summary = await build_group_summary(save=True)
            else:
                await update.message.reply_text(
                    "Please paste the content after your summarize request, or set MONITORED_GROUP_CHAT_ID for group summaries."
                )
                return

            await update.message.reply_text(summary)
            await OperationStatus.done(update, "Summary complete. I will now draft reminder suggestions.")
            await self.draft_manager.propose_from_text(
                update=update,
                source_kind="group_summary",
                content=summary,
                user_instruction=text,
            )
        except Exception as exc:
            await OperationStatus.error(update, f"I hit an error while summarizing: {exc}")

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
            await update.message.reply_text(msg("hackathon_no_history"))
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
            await update.message.reply_text(msg("hackathon_no_text"))
            return

        prompt = hackathon_query_prompt(user_query, corpus_lines)
        answer = await self.run_gpu_task(self.ollama.generate_text, prompt)
        await update.message.reply_text(answer)

    async def _handle_reply_reminder(self, update: Update, text: str) -> bool:
        if not update.message or not update.effective_user:
            return False
        replied = update.message.reply_to_message
        if not replied:
            return False

        details = extract_due_and_priority(text, self.settings.default_timezone)
        missing_fields = []
        if not details.get("priority"):
            missing_fields.append("priority (immediate/high/mid/low)")
        if not details.get("due_at_utc"):
            missing_fields.append("due date/time")
        if missing_fields:
            await update.message.reply_text(
                "To add this as a reminder, include "
                + " and ".join(missing_fields)
                + ". Example: add as reminder high tomorrow 9am"
            )
            return True

        source_text = (replied.text or replied.caption or "").strip()
        if not source_text:
            await update.message.reply_text(msg("error_text_only_reply"))
            return True

        title = self._title_from_reply_text(source_text)
        user_id = self.db.upsert_user(
            update.effective_user.id,
            update.effective_user.username,
            self.settings.default_timezone,
        )
        reminder_id = self.db.create_reminder(
            user_id=user_id,
            source_message_id=None,
            source_kind="reply_message",
            title=title,
            notes=source_text[:1500],
            link=self._extract_first_url(source_text),
            priority=details["priority"],
            due_at_utc=details["due_at_utc"],
            timezone_name=self.settings.default_timezone,
            chat_id_to_notify=update.effective_chat.id,
            recurrence_rule=None,
        )
        await update.message.reply_text(
            format_reminder_brief(reminder_id, title, details["due_at_utc"], self.settings.default_timezone)
        )
        return True

    def _title_from_reply_text(self, source_text: str) -> str:
        skip_patterns = (
            "summary:",
            "possible follow-up actions",
            "schedule:",
            "location:",
            "attire:",
        )
        for raw_line in source_text.splitlines():
            line = raw_line.strip().strip("-*# ")
            if not line:
                continue
            lowered = line.lower().strip("*")
            if any(lowered.startswith(pattern) for pattern in skip_patterns):
                continue
            if lowered.startswith("course:"):
                course_name = line.split(":", 1)[1].strip() if ":" in line else ""
                if course_name:
                    return f"Review {course_name} schedule"
            if len(line) > 80:
                line = line[:80].rstrip() + "..."
            return line
        return "Review replied message"

    async def _handle_reply_edit(self, update: Update, text: str) -> bool:
        if not update.message:
            return False
        replied = update.message.reply_to_message
        if not replied:
            return False

        reminder_id = self._extract_reminder_id_from_text((replied.text or replied.caption or "").strip())
        if reminder_id is None:
            return False

        existing = self.db.get_reminder_by_id_for_chat(reminder_id, update.effective_chat.id)
        if existing is None:
            await update.message.reply_text(msg("error_not_found", id=reminder_id))
            return True

        current = dict(existing)
        details = extract_due_and_priority(text, self.settings.default_timezone)

        new_priority = details["priority"] if details.get("priority") else str(current.get("priority") or "mid")
        new_due = details["due_at_utc"] if details.get("due_at_utc") else str(current.get("due_at_utc") or "")
        new_title = self._extract_field_value(text, "title") or str(current.get("title") or "")

        notes_candidate = self._extract_field_value(text, "notes")
        if notes_candidate is None:
            if "clear notes" in text.lower():
                new_notes = ""
            else:
                new_notes = str(current.get("notes") or "")
        else:
            new_notes = notes_candidate

        link_candidate = self._extract_field_value(text, "link")
        if link_candidate is None:
            if "clear link" in text.lower():
                new_link = ""
            else:
                new_link = str(current.get("link") or "")
        else:
            new_link = link_candidate

        recurrence = str(current.get("recurrence_rule") or "")
        every_match = re.search(r"every\s*:\s*(daily|weekly|monthly|none)\b", text, re.IGNORECASE)
        if every_match:
            parsed_recurrence = every_match.group(1).lower()
            recurrence = "" if parsed_recurrence == "none" else parsed_recurrence

        changed = (
            new_title != str(current.get("title") or "")
            or new_notes != str(current.get("notes") or "")
            or new_link != str(current.get("link") or "")
            or new_priority != str(current.get("priority") or "mid")
            or new_due != str(current.get("due_at_utc") or "")
            or recurrence != str(current.get("recurrence_rule") or "")
        )
        if not changed:
            await update.message.reply_text(
                "I found the reminder ID, but no editable fields were detected. "
                "Try: set to tomorrow 8am high, or title:<text>, notes:<text>, link:<url>, every:weekly"
            )
            return True

        if not new_title.strip() or not new_due:
            await update.message.reply_text(msg("error_edit_must_keep"))
            return True

        ok = self.db.update_reminder_fields_for_chat(
            reminder_id=reminder_id,
            chat_id_to_notify=update.effective_chat.id,
            title=new_title.strip(),
            notes=new_notes,
            link=new_link,
            priority=new_priority,
            due_at_utc=new_due,
            recurrence_rule=recurrence,
        )
        if not ok:
            await update.message.reply_text(msg("error_update_failed", id=reminder_id))
            return True

        await update.message.reply_text(
            format_reminder_brief(reminder_id, new_title, new_due, self.settings.default_timezone)
        )
        return True

    def _extract_reminder_id_from_text(self, text: str) -> int | None:
        match = re.search(r"(?:^|\n)ID\s*:\s*(\d+)\b", text, re.IGNORECASE)
        if not match:
            return None
        try:
            return int(match.group(1))
        except ValueError:
            return None

    def _extract_field_value(self, text: str, field_name: str) -> str | None:
        pattern = rf"{field_name}\s*:\s*(.+?)(?=\s+(?:title|notes|link|p|priority|at|every)\s*:|$)"
        match = re.search(pattern, text, re.IGNORECASE)
        if not match:
            return None
        return match.group(1).strip()

    def _extract_first_url(self, text: str) -> str:
        match = re.search(r"https?://\S+", text)
        if not match:
            return ""
        return match.group(0).rstrip(").,]")
