from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.app.bot_orchestrator import ReminderBot


class FlowStateService:
    def __init__(self, bot: "ReminderBot") -> None:
        self.bot = bot

    def clear_pending_flows(self, chat_id: int, keep: set[str] | None = None) -> None:
        keep_set = keep or set()
        if "add_confirm" not in keep_set:
            self.bot.pending_add_confirmations.pop(chat_id, None)
        if "add_wizard" not in keep_set:
            self.bot.pending_add_wizards.pop(chat_id, None)
        if "edit_wizard" not in keep_set:
            self.bot.pending_edit_wizards.pop(chat_id, None)
        if "model_wizard" not in keep_set:
            self.bot.pending_model_wizards.pop(chat_id, None)
        if "topics_wizard" not in keep_set:
            self.bot.pending_topics_wizards.pop(chat_id, None)
        if "notes_wizard" not in keep_set:
            self.bot.pending_notes_wizards.pop(chat_id, None)
        if "delete_wizard" not in keep_set:
            self.bot.pending_delete_wizards.pop(chat_id, None)
        if "draft" not in keep_set:
            self.bot.reminder_draft_manager.pending_by_chat.pop(chat_id, None)
