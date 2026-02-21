from __future__ import annotations

import unittest
from types import SimpleNamespace

try:
    from src.app.handlers.attachment_input.audio_input_handler import AudioInputHandler
    from src.app.handlers.attachment_input.document_input_handler import DocumentInputHandler
    from src.app.handlers.attachment_input.models import AttachmentRef
    from src.app.handlers.attachment_input.visual_input_handler import VisualInputHandler
except Exception:  # pragma: no cover - optional runtime deps may be missing
    AudioInputHandler = None  # type: ignore[assignment]
    DocumentInputHandler = None  # type: ignore[assignment]
    AttachmentRef = None  # type: ignore[assignment]
    VisualInputHandler = None  # type: ignore[assignment]


class _FakeMessage:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def reply_text(self, text: str, reply_markup=None) -> None:  # noqa: ANN001 - test stub
        self.calls.append(text)


class _FakeDraftManager:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def propose_from_text(self, **kwargs) -> None:  # noqa: ANN003 - test stub
        self.calls.append(kwargs)


async def _run_gpu_task(func, *args, **kwargs):
    return func(*args, **kwargs)


class AttachmentInputModuleTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        if any(dep is None for dep in (AudioInputHandler, DocumentInputHandler, AttachmentRef, VisualInputHandler)):
            self.skipTest("input handlers unavailable")

    async def test_audio_handler_uses_transcript_for_draft_when_no_summary(self) -> None:
        draft_manager = _FakeDraftManager()
        stt = SimpleNamespace(transcribe_bytes=lambda _data, _name: "Call the dentist tomorrow", disabled_reason=lambda: "")
        ollama = SimpleNamespace(generate_text=lambda prompt: f"summary:{prompt[:10]}")

        async def _download(_file_id: str):
            return b"audio-bytes"

        handler = AudioInputHandler(
            ollama=ollama,
            stt=stt,
            draft_manager=draft_manager,
            run_gpu_task=_run_gpu_task,
            download_file_bytes=_download,
        )

        update = SimpleNamespace(message=_FakeMessage())
        attachment = AttachmentRef(kind="audio", file_id="f1", mime_type="audio/ogg", file_name="voice.ogg")
        await handler.handle_audio_attachment_intent(
            update=update,
            text="create reminder from this",
            attachment=attachment,
            wants_summary=False,
            create_reminder_requested=True,
        )

        self.assertEqual(len(draft_manager.calls), 1)
        self.assertEqual(draft_manager.calls[0]["source_kind"], "audio_attachment")
        self.assertEqual(draft_manager.calls[0]["content"], "Call the dentist tomorrow")

    async def test_visual_handler_replies_summary_and_proposes_draft(self) -> None:
        draft_manager = _FakeDraftManager()
        ollama = SimpleNamespace(summarize_image=lambda _img, _text: "Receipt for office supplies")

        async def _download(_file_id: str):
            return b"image-bytes"

        handler = VisualInputHandler(
            ollama=ollama,
            draft_manager=draft_manager,
            run_gpu_task=_run_gpu_task,
            download_file_bytes=_download,
        )

        message = _FakeMessage()
        update = SimpleNamespace(message=message)
        attachment = AttachmentRef(kind="image", file_id="f2", mime_type="image/jpeg", file_name="")
        await handler.handle_image_attachment_intent(
            update=update,
            text="summarize and make reminder",
            attachment=attachment,
            wants_summary=True,
            create_reminder_requested=True,
        )

        self.assertIn("Receipt for office supplies", message.calls)
        self.assertEqual(len(draft_manager.calls), 1)
        self.assertEqual(draft_manager.calls[0]["source_kind"], "image_reply")

    async def test_message_handler_unsupported_attachment_notice(self) -> None:
        draft_manager = _FakeDraftManager()
        ollama = SimpleNamespace(generate_text=lambda _prompt: "unused")

        async def _download(_file_id: str):
            return b""

        handler = DocumentInputHandler(
            ollama=ollama,
            draft_manager=draft_manager,
            run_gpu_task=_run_gpu_task,
            download_file_bytes=_download,
        )

        message = _FakeMessage()
        update = SimpleNamespace(message=message)
        attachment = AttachmentRef(kind="zip", file_id="f3", mime_type="application/zip", file_name="bundle.zip")
        await handler.handle_non_image_attachment_intent(update=update, text="summarize", attachment=attachment)

        self.assertEqual(len(message.calls), 1)
        self.assertIn("not supported yet", message.calls[0])


if __name__ == "__main__":
    unittest.main()
