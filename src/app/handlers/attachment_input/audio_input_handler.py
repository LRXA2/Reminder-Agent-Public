from __future__ import annotations

import logging
import subprocess
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, Awaitable, Callable

from src.app.handlers.operation_status import OperationStatus
from src.app.prompts import audio_transcript_summary_prompt
from src.app.messages import msg

from .attachment_types import AttachmentRef

if TYPE_CHECKING:
    from telegram import Update

    from src.app.handlers.reminder_draft.manager import ReminderDraftManager
    from src.clients.ollama_client import OllamaClient
    from src.clients.stt_client import SttClient


LOGGER = logging.getLogger(__name__)


class AudioInputHandler:
    def __init__(
        self,
        ollama: "OllamaClient",
        stt: "SttClient",
        draft_manager: "ReminderDraftManager",
        run_gpu_task: Callable[..., Awaitable[str]],
        download_file_bytes: Callable[[str], Awaitable[bytes | None]],
    ) -> None:
        self.ollama = ollama
        self.stt = stt
        self.draft_manager = draft_manager
        self.run_gpu_task: Callable[..., Awaitable[str]] = run_gpu_task
        self.download_file_bytes: Callable[[str], Awaitable[bytes | None]] = download_file_bytes

    async def handle_audio_attachment_intent(
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
            await OperationStatus.started(update, msg("status_audio_transcribe"))
        elif create_reminder_requested:
            await OperationStatus.started(update, msg("status_audio_transcribe_draft"))

        audio_bytes = await self.download_file_bytes(attachment.file_id)
        if not audio_bytes:
            await target.reply_text(msg("error_download_audio"))
            return

        stt_bytes = audio_bytes
        stt_file_name = attachment.file_name or "audio.ogg"
        if self._is_mp4_media(attachment):
            await target.reply_text(msg("status_extract_mp4"))
            if not self._has_ffmpeg():
                await target.reply_text(
                    "MP4 audio extraction requires ffmpeg, but it is not installed or not in PATH. "
                    "Install ffmpeg and try again."
                )
                return
            converted = self._convert_mp4_to_wav(audio_bytes)
            if converted is None:
                await target.reply_text(
                    "I could not extract audio from that MP4. Please install ffmpeg and try again."
                )
                return
            stt_bytes = converted
            stt_file_name = "converted_audio.wav"

        transcript = await self.run_gpu_task(self.stt.transcribe_bytes, stt_bytes, stt_file_name)
        if not transcript:
            reason = self.stt.disabled_reason()
            message = "I could not transcribe that audio yet."
            if reason:
                message += f" ({reason})"
            await target.reply_text(message)
            return

        transcript = transcript.strip()
        if wants_summary:
            summary_prompt = audio_transcript_summary_prompt(text, transcript)
            summary = await self.run_gpu_task(self.ollama.generate_text, summary_prompt)
            await target.reply_text(summary)
            content_for_draft = summary
        else:
            content_for_draft = transcript

        await self.draft_manager.propose_from_text(
            update=update,
            source_kind="audio_attachment",
            content=content_for_draft,
            user_instruction=text,
        )

    def _is_mp4_media(self, attachment: AttachmentRef) -> bool:
        mime = (attachment.mime_type or "").lower()
        name = (attachment.file_name or "").lower()
        return mime == "video/mp4" or name.endswith(".mp4")

    def _convert_mp4_to_wav(self, media_bytes: bytes) -> bytes | None:
        input_path = ""
        output_path = ""
        try:
            if not self._has_ffmpeg():
                LOGGER.warning("ffmpeg is not installed or not available in PATH")
                return None

            with tempfile.NamedTemporaryFile(delete=False, suffix=".mp4") as input_file:
                input_file.write(media_bytes)
                input_path = input_file.name

            with tempfile.NamedTemporaryFile(delete=False, suffix=".wav") as output_file:
                output_path = output_file.name

            cmd = [
                "ffmpeg",
                "-y",
                "-i",
                input_path,
                "-vn",
                "-ac",
                "1",
                "-ar",
                "16000",
                "-f",
                "wav",
                output_path,
            ]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
            if result.returncode != 0:
                LOGGER.warning("ffmpeg mp4->wav conversion failed: %s", result.stderr.strip())
                return None

            converted = Path(output_path).read_bytes()
            return converted
        except Exception as exc:
            LOGGER.warning("MP4 audio extraction failed: %s", exc)
            return None
        finally:
            for path in (input_path, output_path):
                if not path:
                    continue
                try:
                    Path(path).unlink(missing_ok=True)
                except Exception:
                    pass

    def _has_ffmpeg(self) -> bool:
        try:
            result = subprocess.run(["ffmpeg", "-version"], capture_output=True, text=True, timeout=8)
            return result.returncode == 0
        except Exception:
            return False
