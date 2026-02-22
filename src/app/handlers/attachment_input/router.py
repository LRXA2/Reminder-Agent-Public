from __future__ import annotations

from typing import TYPE_CHECKING

from telegram import Message

from src.app.handlers.intent_parsing import (
    has_image_summary_intent,
    has_reminder_intent,
    has_summary_intent,
)

from .attachment_types import AttachmentRef

if TYPE_CHECKING:
    from telegram import Update

    from src.app.handlers.attachment_input.audio_input_handler import AudioInputHandler
    from src.app.handlers.attachment_input.document_input_handler import DocumentInputHandler
    from src.app.handlers.attachment_input.visual_input_handler import VisualInputHandler


class AttachmentRouter:
    def __init__(
        self,
        audio_handler: "AudioInputHandler",
        visual_handler: "VisualInputHandler",
        document_handler: "DocumentInputHandler",
    ) -> None:
        self.audio_handler = audio_handler
        self.visual_handler = visual_handler
        self.document_handler = document_handler

    async def handle(self, update: "Update", text: str, allow_current_attachment: bool) -> bool:
        if not update.message or not update.effective_user:
            return False

        replied = update.message.reply_to_message
        attachment = self.extract_attachment_ref(replied) if replied else None
        if not attachment and allow_current_attachment:
            attachment = self.extract_attachment_ref(update.message)
        if not attachment:
            return False

        lowered = text.lower()
        wants_summary = has_summary_intent(lowered) or has_image_summary_intent(lowered)
        create_reminder_requested = has_reminder_intent(lowered)
        if not wants_summary and not create_reminder_requested:
            return False

        if attachment.kind in {"docx", "pdf"}:
            await self.document_handler.handle_document_attachment_intent(
                update=update,
                text=text,
                attachment=attachment,
                wants_summary=wants_summary,
                create_reminder_requested=create_reminder_requested,
            )
            return True

        if attachment.kind == "audio":
            await self.audio_handler.handle_audio_attachment_intent(
                update=update,
                text=text,
                attachment=attachment,
                wants_summary=wants_summary,
                create_reminder_requested=create_reminder_requested,
            )
            return True

        if attachment.kind != "image":
            await self.document_handler.handle_non_image_attachment_intent(update, text, attachment)
            return True

        await self.visual_handler.handle_image_attachment_intent(
            update=update,
            text=text,
            attachment=attachment,
            wants_summary=wants_summary,
            create_reminder_requested=create_reminder_requested,
        )
        return True

    def extract_attachment_ref(self, message: Message | None) -> AttachmentRef | None:
        if message is None:
            return None
        if message.photo:
            return AttachmentRef(kind="image", file_id=message.photo[-1].file_id, mime_type="image/jpeg", file_name="")

        if message.voice:
            mime = (message.voice.mime_type or "audio/ogg").lower()
            return AttachmentRef(kind="audio", file_id=message.voice.file_id, mime_type=mime, file_name="voice_note.ogg")

        if message.audio:
            mime = (message.audio.mime_type or "audio/mpeg").lower()
            name = (message.audio.file_name or "audio").strip()
            return AttachmentRef(kind="audio", file_id=message.audio.file_id, mime_type=mime, file_name=name)

        if message.video:
            mime = (message.video.mime_type or "video/mp4").lower()
            return AttachmentRef(kind="audio", file_id=message.video.file_id, mime_type=mime, file_name="video.mp4")

        if message.video_note:
            return AttachmentRef(kind="audio", file_id=message.video_note.file_id, mime_type="video/mp4", file_name="video_note.mp4")

        document = message.document
        if not document:
            return None
        mime_type = (document.mime_type or "").lower()
        file_name = (document.file_name or "").strip()
        if mime_type.startswith("image/"):
            return AttachmentRef(kind="image", file_id=document.file_id, mime_type=mime_type, file_name=file_name)
        if mime_type == "application/vnd.openxmlformats-officedocument.wordprocessingml.document" or file_name.lower().endswith(
            ".docx"
        ):
            return AttachmentRef(kind="docx", file_id=document.file_id, mime_type=mime_type, file_name=file_name)
        if mime_type == "application/pdf" or file_name.lower().endswith(".pdf"):
            return AttachmentRef(kind="pdf", file_id=document.file_id, mime_type=mime_type, file_name=file_name)
        if mime_type == "video/mp4" or file_name.lower().endswith(".mp4"):
            return AttachmentRef(kind="audio", file_id=document.file_id, mime_type=mime_type, file_name=file_name)
        if mime_type.startswith("audio/"):
            return AttachmentRef(kind="audio", file_id=document.file_id, mime_type=mime_type, file_name=file_name)
        return None
