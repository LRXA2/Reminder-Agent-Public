from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from src.app.messages import msg

from .common import get_reply_target

if TYPE_CHECKING:
    from telegram import Update

    from src.app.handlers.wizards.handler import UiWizardHandler


LOGGER = logging.getLogger(__name__)


class DeleteWizard:
    def __init__(self, ui: "UiWizardHandler") -> None:
        self.ui = ui

    @property
    def bot(self):
        return self.ui.bot

    async def handle(self, update: "Update", text: str) -> bool:
        target = get_reply_target(update)
        if target is None:
            return False
        chat_id = update.effective_chat.id
        state = self.bot.pending_delete_wizards.get(chat_id)
        if not state:
            return False

        raw = (text or "").strip()
        lowered = raw.lower()
        if lowered in {"cancel", "stop"}:
            self.bot.pending_delete_wizards.pop(chat_id, None)
            await target.reply_text("Delete flow cancelled.")
            return True

        step = state.get("step", "id")
        if step == "id":
            try:
                reminder_id = int(raw)
            except ValueError:
                await target.reply_text("Please enter a numeric reminder ID, or `cancel`.")
                return True
            row = self.bot.db.get_reminder_by_id_for_chat(reminder_id, chat_id)
            if row is None:
                await target.reply_text(msg("error_not_found", id=reminder_id))
                self.bot.pending_delete_wizards.pop(chat_id, None)
                return True
            state["id"] = str(reminder_id)
            state["step"] = "confirm"
            await target.reply_text(
                f"Delete reminder #{reminder_id} '{str(row['title'] or '')}'? Reply `yes` to confirm or `cancel`.",
                reply_markup=self.ui._delete_wizard_keyboard(confirm=True),
            )
            return True

        if step == "confirm":
            if lowered in {"yes", "confirm", "delete"}:
                reminder_id = int(state.get("id") or "0")
                await self.delete_reminder_by_id(update, reminder_id)
            else:
                await target.reply_text("Delete cancelled.")
            self.bot.pending_delete_wizards.pop(chat_id, None)
            return True

        self.bot.pending_delete_wizards.pop(chat_id, None)
        return False

    async def delete_reminder_by_id(self, update: "Update", reminder_id: int) -> None:
        target = get_reply_target(update)
        if target is None:
            return
        try:
            event_id = self.bot.db.get_calendar_event_id(reminder_id, provider="google")
            if event_id:
                self.bot.db.add_calendar_event_tombstone(event_id, provider="google")
            await self.bot._sync_calendar_delete(reminder_id)
            ok = self.bot.db.delete_reminder_permanently_for_chat(reminder_id, update.effective_chat.id)
            if ok:
                await target.reply_text(msg("status_deleted", id=reminder_id))
            else:
                await target.reply_text(msg("error_not_found", id=reminder_id))
        except Exception as exc:
            LOGGER.exception("Delete reminder failed for id=%s", reminder_id)
            await target.reply_text(msg("error_delete_failed", error=exc))
