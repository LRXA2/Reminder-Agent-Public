from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from telegram import Update
    from telegram.ext import ContextTypes

    from src.app.reminder_bot import ReminderBot


class ChatPipelineHandler:
    def __init__(self, bot: "ReminderBot") -> None:
        self.bot = bot

    async def normal_chat_handler(self, update: "Update", context: "ContextTypes.DEFAULT_TYPE") -> None:
        if not update.message:
            return
        text = (update.message.text or "").strip()
        if not text:
            return

        for handler in self.pending_workflow_handlers():
            if await handler(update, text):
                return

        handled_attachment_reply = await self.bot.attachment_input_handler.handle_message(
            update,
            text,
            allow_current_attachment=False,
        )
        if handled_attachment_reply:
            return

        await self.bot.text_input_handler.handle_message(
            update,
            parse_add_payload=self.bot._parse_add_payload,
            build_group_summary=self.bot._build_group_summary,
        )

    def pending_workflow_handlers(self):
        return [
            self.bot._handle_pending_model_wizard,
            self.bot._handle_pending_topics_wizard,
            self.bot._handle_pending_notes_wizard,
            self.bot._handle_pending_delete_wizard,
            self.bot._handle_pending_edit_wizard,
            self.bot._handle_pending_add_wizard,
            self.bot._handle_pending_add_confirmation,
            self.bot.reminder_draft_manager.handle_followup,
        ]

    async def attachment_message_handler(self, update: "Update", context: "ContextTypes.DEFAULT_TYPE") -> None:
        if not update.message:
            return
        if update.effective_chat.id != self.bot.settings.personal_chat_id:
            return

        caption = (update.message.caption or "").strip()
        if not caption:
            return

        await self.bot.attachment_input_handler.handle_message(
            update,
            caption,
            allow_current_attachment=True,
        )
