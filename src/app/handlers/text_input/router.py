from __future__ import annotations

from src.app.handlers.intent_parsing import (
    has_edit_intent,
    has_hackathon_query_intent,
    has_reminder_intent,
    has_summary_intent,
)


class TextInputRouter:
    def __init__(self, parent) -> None:
        self.parent = parent

    async def handle_message(self, update, parse_add_payload, build_group_summary) -> bool:
        if not update.message or not update.effective_user:
            return False

        chat_id = update.effective_chat.id
        text = (update.message.text or "").strip()
        if not text:
            return False
        if chat_id != self.parent.settings.personal_chat_id:
            return False

        lowered = text.lower()
        if update.message.reply_to_message and has_edit_intent(lowered):
            handled_edit = await self.parent.reply_handler.handle_reply_edit(update, text)
            if handled_edit:
                return True

        if has_summary_intent(lowered):
            await self.parent.summary_handler.handle_summary_intent(update, text, build_group_summary)
            return True

        if has_hackathon_query_intent(lowered):
            await self.parent.summary_handler.handle_hackathon_query(update, text)
            return True

        if not has_reminder_intent(lowered):
            return False

        if update.message.reply_to_message:
            handled_reply = await self.parent.reply_handler.handle_reply_reminder(update, text)
            if handled_reply:
                return True

        return await self.parent.reminder_handler.handle_text_reminder(update, text, parse_add_payload)
