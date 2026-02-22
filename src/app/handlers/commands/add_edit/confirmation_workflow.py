from __future__ import annotations

from datetime import timezone
from typing import TYPE_CHECKING

from telegram import Update

from src.app.handlers.reminder_formatting import format_due_display, format_reminder_brief
from src.app.messages import msg

if TYPE_CHECKING:
    from src.app.bot_orchestrator import ReminderBot


class AddConfirmationWorkflow:
    def __init__(self, bot: "ReminderBot") -> None:
        self.bot = bot

    async def handle_pending_add_confirmation(self, update: Update, text: str) -> bool:
        if not update.message or not update.effective_user:
            return False
        chat_id = update.effective_chat.id
        queue = self.bot.pending_add_confirmations.get(chat_id)
        if not queue:
            return False
        pending = queue[0]

        lowered = text.strip().lower()
        if lowered in {"cancel", "skip"}:
            queue.pop(0)
            if not queue:
                self.bot.pending_add_confirmations.pop(chat_id, None)
            await update.message.reply_text(msg("status_pending_add_cancelled"))
            return True

        if lowered in {"yes", "confirm", "ok", "okay"}:
            reminder_id = self._create_from_pending(update, chat_id, pending)
            queue.pop(0)
            if not queue:
                self.bot.pending_add_confirmations.pop(chat_id, None)
            await update.message.reply_text(
                format_reminder_brief(reminder_id, pending["title"], pending["due_at_utc"], self.bot.settings.default_timezone)
            )
            await self.bot.calendar_sync_handler.sync_calendar_upsert(reminder_id)
            if queue:
                next_due = format_due_display(queue[0]["due_at_utc"], self.bot.settings.default_timezone)
                await update.message.reply_text(
                    msg("status_due_guess", due_local=next_due, timezone=self.bot.settings.default_timezone)
                )
            return True

        parsed_dt, confidence = self.bot.datetime_resolution_handler.parse_natural_datetime(text)
        if parsed_dt is None:
            await update.message.reply_text(msg("error_due_confirm_parse"))
            return True

        pending["due_at_utc"] = parsed_dt.astimezone(timezone.utc).isoformat()
        if confidence == "high":
            reminder_id = self._create_from_pending(update, chat_id, pending)
            queue.pop(0)
            if not queue:
                self.bot.pending_add_confirmations.pop(chat_id, None)
            await update.message.reply_text(
                format_reminder_brief(reminder_id, pending["title"], pending["due_at_utc"], self.bot.settings.default_timezone)
            )
            await self.bot.calendar_sync_handler.sync_calendar_upsert(reminder_id)
            if queue:
                next_due = format_due_display(queue[0]["due_at_utc"], self.bot.settings.default_timezone)
                await update.message.reply_text(
                    msg("status_due_guess", due_local=next_due, timezone=self.bot.settings.default_timezone)
                )
            return True

        due_local = format_due_display(pending["due_at_utc"], self.bot.settings.default_timezone)
        await update.message.reply_text(
            msg("status_due_recheck", due_local=due_local, timezone=self.bot.settings.default_timezone)
        )
        return True

    def _create_from_pending(self, update: Update, chat_id: int, pending: dict[str, str]) -> int:
        user_id = self.bot.db.upsert_user(update.effective_user.id, update.effective_user.username, self.bot.settings.default_timezone)
        reminder_id = self.bot.db.create_reminder(
            user_id=user_id,
            source_message_id=None,
            source_kind="user_input",
            title=pending["title"],
            topic=pending.get("topic", ""),
            notes="",
            link=pending.get("link", ""),
            priority=pending["priority"],
            due_at_utc=pending["due_at_utc"],
            timezone_name=self.bot.settings.default_timezone,
            chat_id_to_notify=chat_id,
            recurrence_rule=pending["recurrence"],
        )
        self.bot.db.set_reminder_topics_for_chat(
            reminder_id,
            chat_id,
            self.bot.reminder_logic_handler.split_topics(str(pending.get("topic") or "")),
        )
        return reminder_id
