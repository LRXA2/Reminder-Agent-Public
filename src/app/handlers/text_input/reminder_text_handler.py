from __future__ import annotations

from src.app.handlers.reminder_formatting import format_reminder_brief
from src.app.messages import msg


class ReminderTextHandler:
    def __init__(self, parent) -> None:
        self.parent = parent

    async def handle_text_reminder(self, update, text: str, parse_add_payload) -> bool:
        if not update.message or not update.effective_user:
            return False

        chat_id = update.effective_chat.id
        parsed = parse_add_payload(text)
        if parsed.get("error"):
            await update.message.reply_text(msg("error_text_need_due"))
            return True

        if parsed.get("needs_confirmation"):
            await update.message.reply_text(msg("status_use_add_for_confirmation"))
            return True

        topics = self.parent.common.split_topics(str(parsed.get("topic") or ""))
        if self.parent.settings.require_topic_on_add and not topics:
            await update.message.reply_text(msg("error_topic_required"))
            return True
        missing_topics = self.parent.db.has_missing_topics_for_chat(chat_id, topics)
        if missing_topics:
            await update.message.reply_text(self.parent.common.format_missing_topics_message(chat_id, missing_topics))
            return True

        user_id = self.parent.db.upsert_user(
            update.effective_user.id,
            update.effective_user.username,
            self.parent.settings.default_timezone,
        )
        reminder_id = self.parent.db.create_reminder(
            user_id=user_id,
            source_message_id=None,
            source_kind="user_input",
            title=parsed["title"],
            topic=parsed.get("topic", ""),
            notes="",
            link=parsed.get("link", ""),
            priority=parsed["priority"],
            due_at_utc=parsed["due_at_utc"],
            timezone_name=self.parent.settings.default_timezone,
            chat_id_to_notify=chat_id,
            recurrence_rule=parsed["recurrence"],
        )
        self.parent.db.set_reminder_topics_for_chat(reminder_id, chat_id, topics)
        await update.message.reply_text(
            format_reminder_brief(reminder_id, parsed["title"], parsed["due_at_utc"], self.parent.settings.default_timezone)
        )
        if self.parent.on_reminder_created:
            await self.parent.on_reminder_created(reminder_id)
        return True
