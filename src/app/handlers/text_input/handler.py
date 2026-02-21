from __future__ import annotations

from telegram import Update

from src.app.handlers.reminder_draft.manager import ReminderDraftManager
from src.app.handlers.text_input.common import TextInputCommon
from src.app.handlers.text_input.reminder_text_handler import ReminderTextHandler
from src.app.handlers.text_input.reply_workflow_handler import ReplyWorkflowHandler
from src.app.handlers.text_input.router import TextInputRouter
from src.app.handlers.text_input.summary_handler import TextSummaryHandler
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
        on_reminder_created=None,
        on_reminder_updated=None,
    ):
        self.db = db
        self.ollama = ollama
        self.settings = settings
        self.draft_manager = draft_manager
        self.run_gpu_task = run_gpu_task
        self.on_reminder_created = on_reminder_created
        self.on_reminder_updated = on_reminder_updated

        self.common = TextInputCommon(self.db)
        self.summary_handler = TextSummaryHandler(self)
        self.reply_handler = ReplyWorkflowHandler(self)
        self.reminder_handler = ReminderTextHandler(self)
        self.router = TextInputRouter(self)

    async def handle_message(self, update: Update, parse_add_payload, build_group_summary) -> bool:
        return await self.router.handle_message(update, parse_add_payload, build_group_summary)
