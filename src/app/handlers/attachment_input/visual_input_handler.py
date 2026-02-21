from __future__ import annotations

from typing import TYPE_CHECKING, Awaitable, Callable

from src.app.handlers.operation_status import OperationStatus
from src.app.messages import msg

from .models import AttachmentRef

if TYPE_CHECKING:
    from telegram import Update

    from src.app.handlers.reminder_draft.manager import ReminderDraftManager
    from src.clients.ollama_client import OllamaClient


class VisualInputHandler:
    def __init__(
        self,
        ollama: "OllamaClient",
        draft_manager: "ReminderDraftManager",
        run_gpu_task: Callable[..., Awaitable[str]],
        download_file_bytes: Callable[[str], Awaitable[bytes | None]],
    ) -> None:
        self.ollama = ollama
        self.draft_manager = draft_manager
        self.run_gpu_task: Callable[..., Awaitable[str]] = run_gpu_task
        self.download_file_bytes: Callable[[str], Awaitable[bytes | None]] = download_file_bytes

    async def handle_image_attachment_intent(
        self,
        update: "Update",
        text: str,
        attachment: AttachmentRef,
        wants_summary: bool,
        create_reminder_requested: bool,
    ) -> None:
        if not update.message:
            return
        target = update.message
        if wants_summary:
            await OperationStatus.started(update, msg("status_image_analyzing"))
        elif create_reminder_requested:
            await OperationStatus.started(update, msg("status_image_analyzing_draft"))

        image_bytes = await self.download_file_bytes(attachment.file_id)
        if not image_bytes:
            await target.reply_text(msg("error_download_image"))
            return

        summary_text = ""
        if wants_summary or create_reminder_requested:
            summary_text = await self.run_gpu_task(self.ollama.summarize_image, image_bytes, text)
        if wants_summary and summary_text:
            await target.reply_text(summary_text)

        await self.draft_manager.propose_from_text(
            update=update,
            source_kind="image_reply",
            content=summary_text,
            user_instruction=text,
        )
