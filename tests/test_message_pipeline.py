from __future__ import annotations

import unittest
from types import SimpleNamespace

from src.app.handlers.runtime.message_pipeline import ChatPipelineHandler


class _FakeMessage:
    def __init__(self, text: str = "", caption: str = "") -> None:
        self.text = text
        self.caption = caption


class _CallRecorder:
    def __init__(self, result=False) -> None:
        self.calls: list[tuple] = []
        self.result = result

    async def __call__(self, *args, **kwargs):  # noqa: ANN002,ANN003 - test stub
        self.calls.append((args, kwargs))
        return self.result


class MessagePipelineTests(unittest.IsolatedAsyncioTestCase):
    async def test_pending_workflow_short_circuits_pipeline(self) -> None:
        pending_true = _CallRecorder(result=True)
        attachment = _CallRecorder(result=False)
        text_input = _CallRecorder(result=False)

        bot = SimpleNamespace(
            list_sync_model_handler=SimpleNamespace(handle_pending_model_wizard=pending_true),
            ui_wizard_handler=SimpleNamespace(
                _handle_pending_topics_wizard=_CallRecorder(result=False),
                _handle_pending_notes_wizard=_CallRecorder(result=False),
                _handle_pending_delete_wizard=_CallRecorder(result=False),
                _handle_pending_edit_wizard=_CallRecorder(result=False),
            ),
            add_edit_handler=SimpleNamespace(
                handle_pending_add_wizard=_CallRecorder(result=False),
                handle_pending_add_confirmation=_CallRecorder(result=False),
                parse_add_payload=lambda _x: {},
            ),
            reminder_draft_manager=SimpleNamespace(handle_followup=_CallRecorder(result=False)),
            attachment_input_handler=SimpleNamespace(handle_message=attachment),
            text_input_handler=SimpleNamespace(handle_message=text_input),
            job_runner=SimpleNamespace(build_group_summary=lambda *_a, **_k: ""),
            settings=SimpleNamespace(personal_chat_id=1),
        )
        handler = ChatPipelineHandler(bot)
        update = SimpleNamespace(message=_FakeMessage(text="hello"), effective_chat=SimpleNamespace(id=1))

        await handler.normal_chat_handler(update, SimpleNamespace())

        self.assertEqual(len(pending_true.calls), 1)
        self.assertEqual(len(attachment.calls), 0)
        self.assertEqual(len(text_input.calls), 0)

    async def test_attachment_path_runs_before_text_handler(self) -> None:
        attachment = _CallRecorder(result=True)
        text_input = _CallRecorder(result=False)
        bot = SimpleNamespace(
            list_sync_model_handler=SimpleNamespace(handle_pending_model_wizard=_CallRecorder(result=False)),
            ui_wizard_handler=SimpleNamespace(
                _handle_pending_topics_wizard=_CallRecorder(result=False),
                _handle_pending_notes_wizard=_CallRecorder(result=False),
                _handle_pending_delete_wizard=_CallRecorder(result=False),
                _handle_pending_edit_wizard=_CallRecorder(result=False),
            ),
            add_edit_handler=SimpleNamespace(
                handle_pending_add_wizard=_CallRecorder(result=False),
                handle_pending_add_confirmation=_CallRecorder(result=False),
                parse_add_payload=lambda _x: {},
            ),
            reminder_draft_manager=SimpleNamespace(handle_followup=_CallRecorder(result=False)),
            attachment_input_handler=SimpleNamespace(handle_message=attachment),
            text_input_handler=SimpleNamespace(handle_message=text_input),
            job_runner=SimpleNamespace(build_group_summary=lambda *_a, **_k: ""),
            settings=SimpleNamespace(personal_chat_id=1),
        )
        handler = ChatPipelineHandler(bot)
        update = SimpleNamespace(message=_FakeMessage(text="hello"), effective_chat=SimpleNamespace(id=1))

        await handler.normal_chat_handler(update, SimpleNamespace())

        self.assertEqual(len(attachment.calls), 1)
        self.assertEqual(len(text_input.calls), 0)

    async def test_text_handler_runs_when_no_other_workflow_handles(self) -> None:
        attachment = _CallRecorder(result=False)
        text_input = _CallRecorder(result=True)
        bot = SimpleNamespace(
            list_sync_model_handler=SimpleNamespace(handle_pending_model_wizard=_CallRecorder(result=False)),
            ui_wizard_handler=SimpleNamespace(
                _handle_pending_topics_wizard=_CallRecorder(result=False),
                _handle_pending_notes_wizard=_CallRecorder(result=False),
                _handle_pending_delete_wizard=_CallRecorder(result=False),
                _handle_pending_edit_wizard=_CallRecorder(result=False),
            ),
            add_edit_handler=SimpleNamespace(
                handle_pending_add_wizard=_CallRecorder(result=False),
                handle_pending_add_confirmation=_CallRecorder(result=False),
                parse_add_payload=lambda _x: {"title": "x"},
            ),
            reminder_draft_manager=SimpleNamespace(handle_followup=_CallRecorder(result=False)),
            attachment_input_handler=SimpleNamespace(handle_message=attachment),
            text_input_handler=SimpleNamespace(handle_message=text_input),
            job_runner=SimpleNamespace(build_group_summary=lambda *_a, **_k: "summary"),
            settings=SimpleNamespace(personal_chat_id=1),
        )
        handler = ChatPipelineHandler(bot)
        update = SimpleNamespace(message=_FakeMessage(text="hello"), effective_chat=SimpleNamespace(id=1))

        await handler.normal_chat_handler(update, SimpleNamespace())

        self.assertEqual(len(text_input.calls), 1)
        args, kwargs = text_input.calls[0]
        self.assertIs(args[0], update)
        self.assertIn("parse_add_payload", kwargs)
        self.assertIn("build_group_summary", kwargs)

    async def test_attachment_message_handler_filters_chat_and_caption(self) -> None:
        attachment = _CallRecorder(result=True)
        bot = SimpleNamespace(
            attachment_input_handler=SimpleNamespace(handle_message=attachment),
            settings=SimpleNamespace(personal_chat_id=10),
        )
        handler = ChatPipelineHandler(bot)

        other_chat_update = SimpleNamespace(message=_FakeMessage(caption="remind"), effective_chat=SimpleNamespace(id=11))
        await handler.attachment_message_handler(other_chat_update, SimpleNamespace())
        self.assertEqual(len(attachment.calls), 0)

        no_caption_update = SimpleNamespace(message=_FakeMessage(caption=""), effective_chat=SimpleNamespace(id=10))
        await handler.attachment_message_handler(no_caption_update, SimpleNamespace())
        self.assertEqual(len(attachment.calls), 0)

        good_update = SimpleNamespace(message=_FakeMessage(caption="summarize"), effective_chat=SimpleNamespace(id=10))
        await handler.attachment_message_handler(good_update, SimpleNamespace())
        self.assertEqual(len(attachment.calls), 1)


if __name__ == "__main__":
    unittest.main()
