from __future__ import annotations

from typing import TYPE_CHECKING

from telegram import InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from src.app.handlers.wizards.delete_wizard import DeleteWizard
from src.app.handlers.wizards.edit_wizard import EditWizard
from src.app.handlers.wizards.keyboards import (
    delete_wizard_keyboard,
    edit_topic_keyboard,
    edit_wizard_keyboard,
    notes_wizard_keyboard,
    topics_wizard_keyboard,
)
from src.app.handlers.wizards.notes_wizard import NotesWizard
from src.app.handlers.wizards.topics_wizard import TopicsWizard
from src.app.handlers.wizards.ui_router import UiRouter

if TYPE_CHECKING:
    from src.app.bot_orchestrator import ReminderBot


class UiWizardHandler:
    def __init__(self, bot: "ReminderBot") -> None:
        self.bot = bot
        self.router = UiRouter(self)
        self.edit_wizard = EditWizard(self)
        self.delete_wizard = DeleteWizard(self)
        self.notes_wizard = NotesWizard(self)
        self.topics_wizard = TopicsWizard(self)

    async def ui_callback_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await self.router.handle(update, context)

    def _notes_wizard_keyboard(self) -> InlineKeyboardMarkup:
        return notes_wizard_keyboard()

    def _topics_wizard_keyboard(self) -> InlineKeyboardMarkup:
        return topics_wizard_keyboard()

    def _delete_wizard_keyboard(self, confirm: bool = False) -> InlineKeyboardMarkup:
        return delete_wizard_keyboard(confirm=confirm)

    def _edit_wizard_keyboard(self) -> InlineKeyboardMarkup:
        return edit_wizard_keyboard()

    def _edit_topic_keyboard(self) -> InlineKeyboardMarkup:
        return edit_topic_keyboard()

    async def _handle_pending_edit_wizard(self, update: Update, text: str) -> bool:
        return await self.edit_wizard.handle(update, text)

    def _render_edit_wizard_menu(self, state: dict[str, str]) -> str:
        return self.edit_wizard.render_menu(state)

    async def _handle_pending_delete_wizard(self, update: Update, text: str) -> bool:
        return await self.delete_wizard.handle(update, text)

    async def _handle_pending_notes_wizard(self, update: Update, text: str) -> bool:
        return await self.notes_wizard.handle(update, text)

    async def _handle_pending_topics_wizard(self, update: Update, text: str) -> bool:
        return await self.topics_wizard.handle(update, text)

    async def _delete_reminder_by_id(self, update: Update, reminder_id: int) -> None:
        await self.delete_wizard.delete_reminder_by_id(update, reminder_id)

    def _collect_notes_candidates(self, chat_id: int) -> list[dict]:
        return self.notes_wizard.collect_candidates(chat_id)

    async def _update_reminder_notes(self, chat_id: int, reminder_id: int, notes: str) -> bool:
        return await self.notes_wizard.update_notes(chat_id, reminder_id, notes)
