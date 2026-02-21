from __future__ import annotations

import zipfile
from io import BytesIO
from typing import TYPE_CHECKING, Awaitable, Callable
from xml.etree import ElementTree as ET

from src.app.handlers.operation_status import OperationStatus
from src.app.prompts import document_summary_prompt
from src.app.messages import msg

from .models import AttachmentRef

if TYPE_CHECKING:
    from telegram import Update

    from src.app.handlers.reminder_draft.manager import ReminderDraftManager
    from src.clients.ollama_client import OllamaClient


class DocumentInputHandler:
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

    async def handle_document_attachment_intent(
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
            await OperationStatus.started(update, msg("status_doc_analyzing"))
        elif create_reminder_requested:
            await OperationStatus.started(update, msg("status_doc_analyzing_draft"))

        file_bytes = await self.download_file_bytes(attachment.file_id)
        if not file_bytes:
            await target.reply_text(msg("error_download_doc"))
            return

        document_text = self._extract_document_text(attachment, file_bytes)
        if not document_text:
            await target.reply_text(
                "I could not extract readable text from that document yet. "
                "For PDFs, make sure `pypdf` is installed and the PDF contains selectable text."
            )
            return

        summary_text = ""
        if wants_summary or create_reminder_requested:
            summary_text = await self.run_gpu_task(self._summarize_document_text, document_text, text, attachment)
        if wants_summary and summary_text:
            await target.reply_text(summary_text)

        await self.draft_manager.propose_from_text(
            update=update,
            source_kind=f"{attachment.kind}_attachment",
            content=summary_text or document_text[:4000],
            user_instruction=text,
        )

    async def handle_non_image_attachment_intent(
        self,
        update: "Update",
        text: str,
        attachment: AttachmentRef,
    ) -> None:
        if not update.message:
            return
        kind_label = attachment.kind.upper()
        await update.message.reply_text(
            f"I detected a {kind_label} attachment ({attachment.file_name or attachment.mime_type}). "
            "AI parsing is enabled for image, document, and audio attachments. "
            "This attachment type is not supported yet."
        )

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
        prompt = document_summary_prompt(attachment.kind, user_instruction, excerpt)
        return self.ollama.generate_text(prompt)

    def _clip_text(self, text: str, max_chars: int) -> str:
        cleaned = text.strip()
        if len(cleaned) <= max_chars:
            return cleaned
        return cleaned[:max_chars] + "\n...[truncated]"
