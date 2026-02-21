from __future__ import annotations

from typing import TYPE_CHECKING

from telegram.ext import ContextTypes

if TYPE_CHECKING:
    from telegram import Update

    from src.app.handlers.wizards.handler import UiWizardHandler


class UiRouter:
    def __init__(self, ui: "UiWizardHandler") -> None:
        self.ui = ui

    @property
    def bot(self):
        return self.ui.bot

    async def handle(self, update: "Update", context: ContextTypes.DEFAULT_TYPE) -> None:
        query = update.callback_query
        if query is None:
            return
        if self.bot.settings.allowed_telegram_user_ids and query.from_user:
            if int(query.from_user.id) not in set(self.bot.settings.allowed_telegram_user_ids):
                await query.answer("Not authorized", show_alert=True)
                return

        data = (query.data or "").strip().lower()
        await query.answer()

        if data.startswith("ui:sync:"):
            mode = data.split(":", 2)[2]
            await self.bot._run_sync_mode(update, mode)
            return

        if data.startswith("ui:list:"):
            mode = data.split(":", 2)[2]
            await self.bot._run_list_mode(update, mode)
            return

        if data.startswith("ui:notes:"):
            action = data.split(":", 2)[2]
            if action == "menu":
                self.bot.pending_notes_wizards[update.effective_chat.id] = {"mode": "menu"}
                await query.message.reply_text(
                    "Notes wizard. Choose an action:",
                    reply_markup=self.ui._notes_wizard_keyboard(),
                )
                return
            if action == "list":
                await self.ui._handle_pending_notes_wizard(update, "list")
                return
            if action == "cancel":
                await self.ui._handle_pending_notes_wizard(update, "cancel")
                return
            if action in {"view", "edit", "clear"}:
                await query.message.reply_text(f"Type `{action} <id>` to continue.")
                return

        if data.startswith("ui:topics:"):
            action = data.split(":", 2)[2]
            if action == "menu":
                self.bot.pending_topics_wizards[update.effective_chat.id] = {"mode": "menu"}
                await query.message.reply_text(
                    "Topics wizard. Choose an action:",
                    reply_markup=self.ui._topics_wizard_keyboard(),
                )
                return
            if action in {"list", "list_all", "cancel"}:
                mapped = "list all" if action == "list_all" else action
                await self.ui._handle_pending_topics_wizard(update, mapped)
                return
            if action in {"create", "rename", "delete", "merge"}:
                await query.message.reply_text(f"Type `{action} ...` to continue.")
                return

        if data.startswith("ui:delete:"):
            action = data.split(":", 2)[2]
            if action == "menu":
                self.bot.pending_delete_wizards[update.effective_chat.id] = {"step": "id"}
                await query.message.reply_text(
                    "Delete wizard: enter reminder ID to delete, or cancel.",
                    reply_markup=self.ui._delete_wizard_keyboard(),
                )
                return
            if action in {"cancel", "confirm"}:
                mapped = "yes" if action == "confirm" else "cancel"
                await self.ui._handle_pending_delete_wizard(update, mapped)
                return

        if data.startswith("ui:edit:"):
            action = data.split(":", 2)[2]
            mapping = {
                "menu": "menu",
                "title": "title",
                "due": "due",
                "priority": "priority",
                "topic": "topic",
                "interval": "interval",
                "link": "link",
                "notes": "notes",
                "save": "save",
                "cancel": "cancel",
                "topic_add": "topic_add",
                "topic_remove": "topic_remove",
                "topic_replace": "topic_replace",
                "topic_clear": "topic_clear",
                "topic_back": "topic_back",
            }
            mapped = mapping.get(action)
            if mapped is None:
                await query.message.reply_text("Unknown edit action.")
                return
            await self.ui._handle_pending_edit_wizard(update, mapped)
