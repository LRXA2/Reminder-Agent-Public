from __future__ import annotations

import json
import logging
import re
import zipfile
from dataclasses import dataclass
from io import BytesIO
from xml.etree import ElementTree as ET

from telegram import Message, Update
from telegram.ext import Application

from src.app.handlers.intent_parsing import (
    has_image_summary_intent,
    has_reminder_intent,
    has_summary_intent,
)
from src.app.handlers.reminder_draft_manager import ReminderDraftManager
from src.clients.ollama_client import OllamaClient
from src.clients.stt_client import SttClient
from src.core.config import Settings
from src.storage.database import Database


LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class AttachmentRef:
    kind: str
    file_id: str
    mime_type: str
    file_name: str


class AttachmentInputHandler:
    def __init__(
        self,
        app: Application,
        db: Database,
        ollama: OllamaClient,
        stt: SttClient,
        settings: Settings,
        draft_manager: ReminderDraftManager,
    ):
        self.app = app
        self.db = db
        self.ollama = ollama
        self.stt = stt
        self.settings = settings
        self.draft_manager = draft_manager

    async def handle_message(self, update: Update, text: str, allow_current_attachment: bool) -> bool:
        if not update.message or not update.effective_user:
            return False

        replied = update.message.reply_to_message
        attachment = self._extract_attachment_ref(replied) if replied else None
        if not attachment and allow_current_attachment:
            attachment = self._extract_attachment_ref(update.message)
        if not attachment:
            return False

        lowered = text.lower()
        wants_summary = has_summary_intent(lowered) or has_image_summary_intent(lowered)
        create_reminder_requested = has_reminder_intent(lowered)
        if not wants_summary and not create_reminder_requested:
            return False

        if attachment.kind in {"docx", "pdf"}:
            await self._handle_document_attachment_intent(
                update=update,
                text=text,
                attachment=attachment,
                wants_summary=wants_summary,
                create_reminder_requested=create_reminder_requested,
            )
            return True

        if attachment.kind == "audio":
            await self._handle_audio_attachment_intent(
                update=update,
                text=text,
                attachment=attachment,
                wants_summary=wants_summary,
                create_reminder_requested=create_reminder_requested,
            )
            return True

        if attachment.kind != "image":
            await self._handle_non_image_attachment_intent(update, text, attachment)
            return True

        if wants_summary:
            await update.message.reply_text("Got it - analyzing image now...")
        elif create_reminder_requested:
            await update.message.reply_text("Got it - analyzing image and drafting reminders...")

        image_bytes = await self._download_file_bytes(attachment.file_id)
        if not image_bytes:
            await update.message.reply_text("I could not download that image. Please try again.")
            return True

        summary_text = ""
        if wants_summary or create_reminder_requested:
            summary_text = self.ollama.summarize_image(image_bytes=image_bytes, user_instruction=text)
        if wants_summary and summary_text:
            await update.message.reply_text(summary_text)

        await self.draft_manager.propose_from_text(
            update=update,
            source_kind="image_reply",
            content=summary_text,
            user_instruction=text,
        )
        return True

    async def _handle_document_attachment_intent(
        self,
        update: Update,
        text: str,
        attachment: AttachmentRef,
        wants_summary: bool,
        create_reminder_requested: bool,
    ) -> None:
        if wants_summary:
            await update.message.reply_text("Got it - reading and summarizing this document now...")
        elif create_reminder_requested:
            await update.message.reply_text("Got it - reading this document and creating your reminder...")

        file_bytes = await self._download_file_bytes(attachment.file_id)
        if not file_bytes:
            await update.message.reply_text("I could not download that document. Please try again.")
            return

        document_text = self._extract_document_text(attachment, file_bytes)
        if not document_text:
            await update.message.reply_text(
                "I could not extract readable text from that document yet. "
                "For PDFs, make sure `pypdf` is installed and the PDF contains selectable text."
            )
            return

        summary_text = ""
        if wants_summary or create_reminder_requested:
            summary_text = self._summarize_document_text(document_text, text, attachment)
        if wants_summary and summary_text:
            await update.message.reply_text(summary_text)

        await self.draft_manager.propose_from_text(
            update=update,
            source_kind=f"{attachment.kind}_attachment",
            content=summary_text or document_text[:4000],
            user_instruction=text,
        )

    async def _handle_audio_attachment_intent(
        self,
        update: Update,
        text: str,
        attachment: AttachmentRef,
        wants_summary: bool,
        create_reminder_requested: bool,
    ) -> None:
        if wants_summary:
            await update.message.reply_text("Got it - transcribing and summarizing this audio now...")
        elif create_reminder_requested:
            await update.message.reply_text("Got it - transcribing audio and drafting reminders...")

        audio_bytes = await self._download_file_bytes(attachment.file_id)
        if not audio_bytes:
            await update.message.reply_text("I could not download that audio file. Please try again.")
            return

        transcript = self.stt.transcribe_bytes(audio_bytes, attachment.file_name)
        if not transcript:
            reason = self.stt.disabled_reason()
            message = "I could not transcribe that audio yet."
            if reason:
                message += f" ({reason})"
            await update.message.reply_text(message)
            return

        transcript = transcript.strip()
        if wants_summary:
            summary_prompt = (
                "Summarize this transcript for reminders. Return concise markdown with Key points, "
                "Deadlines/Dates, and Action items.\n\n"
                f"User request: {text}\n\nTranscript:\n{transcript[:22000]}"
            )
            summary = self.ollama.generate_text(summary_prompt)
            await update.message.reply_text(summary)
            content_for_draft = summary
        else:
            content_for_draft = transcript

        await self.draft_manager.propose_from_text(
            update=update,
            source_kind="audio_attachment",
            content=content_for_draft,
            user_instruction=text,
        )

    async def _handle_non_image_attachment_intent(
        self,
        update: Update,
        text: str,
        attachment: AttachmentRef,
    ) -> None:
        kind_label = attachment.kind.upper()
        await update.message.reply_text(
            f"I detected a {kind_label} attachment ({attachment.file_name or attachment.mime_type}). "
            "AI parsing is enabled for image, document, and audio attachments. "
            "This attachment type is not supported yet."
        )

    def _extract_attachment_ref(self, message: Message | None) -> AttachmentRef | None:
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
        if mime_type.startswith("audio/"):
            return AttachmentRef(kind="audio", file_id=document.file_id, mime_type=mime_type, file_name=file_name)
        return None

    async def _download_file_bytes(self, file_id: str) -> bytes | None:
        try:
            file_obj = await self.app.bot.get_file(file_id)
            data = await file_obj.download_as_bytearray()
            return bytes(data)
        except Exception as exc:
            LOGGER.exception("Failed to download attachment for processing: %s", exc)
            return None

    def _extract_document_text(self, attachment: AttachmentRef, file_bytes: bytes) -> str:
        if attachment.kind == "docx":
            return self._extract_docx_text(file_bytes)
        if attachment.kind == "pdf":
            return self._extract_pdf_text(file_bytes)
        return ""

    def _extract_docx_text(self, file_bytes: bytes) -> str:
        try:
            with zipfile.ZipFile(BytesIO(file_bytes)) as archive:
                xml_bytes = archive.read("word/document.xml")
        except Exception:
            return ""

        try:
            root = ET.fromstring(xml_bytes)
        except Exception:
            return ""

        ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
        paragraphs: list[str] = []
        for paragraph in root.findall(".//w:p", ns):
            parts = [node.text for node in paragraph.findall(".//w:t", ns) if node.text]
            if parts:
                paragraphs.append("".join(parts))
        return "\n".join(paragraphs).strip()

    def _extract_pdf_text(self, file_bytes: bytes) -> str:
        try:
            from pypdf import PdfReader
        except Exception:
            return ""

        try:
            reader = PdfReader(BytesIO(file_bytes))
        except Exception:
            return ""

        pages: list[str] = []
        for page in reader.pages[:60]:
            text = (page.extract_text() or "").strip()
            if text:
                pages.append(text)
        return "\n\n".join(pages).strip()

    def _summarize_document_text(self, document_text: str, user_instruction: str, attachment: AttachmentRef) -> str:
        excerpt = self._clip_text(document_text, 18000)
        prompt = (
            f"Summarize this {attachment.kind.upper()} for a reminder assistant. "
            "Return concise markdown with: Key points, Deadlines/Dates, Action items. "
            f"User request: {user_instruction}\n\n"
            "Document content:\n"
            f"{excerpt}"
        )
        return self.ollama.generate_text(prompt)

    def _extract_reminder_from_document_text(
        self,
        document_text: str,
        user_instruction: str,
        attachment: AttachmentRef,
    ) -> dict[str, str]:
        excerpt = self._clip_text(document_text, 14000)
        prompt = (
            f"Extract a single best reminder from this {attachment.kind.upper()} and return STRICT JSON only. "
            "Format: {\"title\": \"...\", \"notes\": \"...\"}. "
            "Rules: title 3-12 words, actionable, and not a generic heading like 'Summary'; notes <= 280 chars. "
            f"User instruction: {user_instruction}\n\n"
            "Document content:\n"
            f"{excerpt}"
        )
        raw = self.ollama.generate_text(prompt)
        parsed = self._parse_json_object(raw)
        if parsed and (parsed.get("title") or "").strip():
            return {
                "title": self._clamp_words((parsed.get("title") or "").strip(), 12),
                "notes": (parsed.get("notes") or "").strip()[:280],
            }
        fallback_title = "Review document highlights"
        return {"title": fallback_title, "notes": raw.strip()[:280]}

    def _parse_json_object(self, text: str) -> dict[str, str] | None:
        try:
            loaded = json.loads(text)
            if isinstance(loaded, dict):
                return {str(k): str(v) for k, v in loaded.items()}
        except Exception:
            pass

        match = re.search(r"\{[\s\S]*\}", text)
        if not match:
            return None
        try:
            loaded = json.loads(match.group(0))
        except Exception:
            return None
        if not isinstance(loaded, dict):
            return None
        return {str(k): str(v) for k, v in loaded.items()}

    def _clip_text(self, text: str, max_chars: int) -> str:
        cleaned = text.strip()
        if len(cleaned) <= max_chars:
            return cleaned
        return cleaned[:max_chars] + "\n...[truncated]"

    def _clamp_words(self, text: str, max_words: int) -> str:
        words = text.split()
        if len(words) <= max_words:
            return text.strip()
        return " ".join(words[:max_words]).strip()
