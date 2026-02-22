from __future__ import annotations

from typing import TYPE_CHECKING

from telegram import Update
from telegram.ext import ContextTypes

from src.app.messages import msg

if TYPE_CHECKING:
    from src.app.bot_orchestrator import ReminderBot


class CompletionDeleteHandler:
    def __init__(self, bot: "ReminderBot") -> None:
        self.bot = bot

    async def done_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not update.message:
            return
        self.bot.flow_state_service.clear_pending_flows(update.effective_chat.id)
        if not context.args:
            await update.message.reply_text(msg("usage_done"))
            return
        try:
            reminder_id = int(context.args[0])
        except ValueError:
            await update.message.reply_text(msg("error_id_number"))
            return

        ok = self.bot.db.mark_done_and_archive_for_chat(reminder_id, update.effective_chat.id)
        if ok:
            await update.message.reply_text(msg("status_done_archived", id=reminder_id))
            await self.bot.calendar_sync_handler.sync_calendar_delete(reminder_id)
        else:
            await update.message.reply_text(msg("error_done_not_found", id=reminder_id))

    async def delete_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not update.message:
            return
        self.bot.flow_state_service.clear_pending_flows(update.effective_chat.id, keep={"delete_wizard"})
        if not context.args:
            self.bot.pending_delete_wizards[update.effective_chat.id] = {"step": "id"}
            await update.message.reply_text(
                "Delete wizard: enter reminder ID to delete, or `cancel`.",
                reply_markup=self.bot.ui_wizard_handler._delete_wizard_keyboard(),
            )
            return
        try:
            reminder_id = int(context.args[0])
        except ValueError:
            await update.message.reply_text(msg("error_id_number"))
            return
        await self.bot.ui_wizard_handler._delete_reminder_by_id(update, reminder_id)
