from __future__ import annotations

import logging

from telegram import Update
from telegram.ext import Application

from src.app.handlers.attachment_input.audio_input_handler import AudioInputHandler
from src.app.handlers.attachment_input.document_input_handler import DocumentInputHandler
from src.app.handlers.attachment_input.router import AttachmentRouter
from src.app.handlers.attachment_input.visual_input_handler import VisualInputHandler
from src.app.handlers.reminder_draft.manager import ReminderDraftManager
from src.clients.ollama_client import OllamaClient
from src.clients.stt_client import SttClient
from src.core.config import Settings
from src.storage.database import Database


LOGGER = logging.getLogger(__name__)


class AttachmentInputHandler:
    def __init__(
        self,
        app: Application,
        db: Database,
        ollama: OllamaClient,
        stt: SttClient,
        settings: Settings,
        draft_manager: ReminderDraftManager,
        run_gpu_task,
    ):
        self.app = app
        self.db = db
        self.ollama = ollama
        self.stt = stt
        self.settings = settings
        self.draft_manager = draft_manager
        self.run_gpu_task = run_gpu_task

        self.audio_input_handler = AudioInputHandler(
            ollama=self.ollama,
            stt=self.stt,
            draft_manager=self.draft_manager,
            run_gpu_task=self.run_gpu_task,
            download_file_bytes=self._download_file_bytes,
        )
        self.visual_input_handler = VisualInputHandler(
            ollama=self.ollama,
            draft_manager=self.draft_manager,
            run_gpu_task=self.run_gpu_task,
            download_file_bytes=self._download_file_bytes,
        )
        self.document_input_handler = DocumentInputHandler(
            ollama=self.ollama,
            draft_manager=self.draft_manager,
            run_gpu_task=self.run_gpu_task,
            download_file_bytes=self._download_file_bytes,
        )
        self.router = AttachmentRouter(
            audio_handler=self.audio_input_handler,
            visual_handler=self.visual_input_handler,
            document_handler=self.document_input_handler,
        )

    async def handle_message(self, update: Update, text: str, allow_current_attachment: bool) -> bool:
        return await self.router.handle(update, text, allow_current_attachment)

    async def _download_file_bytes(self, file_id: str) -> bytes | None:
        try:
            file_obj = await self.app.bot.get_file(file_id)
            data = await file_obj.download_as_bytearray()
            return bytes(data)
        except Exception as exc:
            LOGGER.exception("Failed to download attachment for processing: %s", exc)
            return None
