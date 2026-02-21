from __future__ import annotations

import unittest
from types import SimpleNamespace

try:
    from src.app.handlers.text_input import router as router_module
    from src.app.handlers.text_input.router import TextInputRouter
except Exception:  # pragma: no cover - optional runtime deps may be missing
    router_module = None  # type: ignore[assignment]
    TextInputRouter = None  # type: ignore[assignment]


class _AsyncRecorder:
    def __init__(self, result=False) -> None:
        self.result = result
        self.calls: list[tuple] = []

    async def __call__(self, *args, **kwargs):  # noqa: ANN002,ANN003 - test stub
        self.calls.append((args, kwargs))
        return self.result


class _PatchIntents:
    def __init__(self, **values) -> None:
        self.values = values
        self.originals = {}

    def __enter__(self):
        for name, value in self.values.items():
            self.originals[name] = getattr(router_module, name)
            setattr(router_module, name, value)

    def __exit__(self, exc_type, exc, tb):
        for name, value in self.originals.items():
            setattr(router_module, name, value)


class TextInputRouterTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        if router_module is None or TextInputRouter is None:
            self.skipTest("text input router dependencies unavailable")

    def _make_parent(self):
        return SimpleNamespace(
            settings=SimpleNamespace(personal_chat_id=7),
            reply_handler=SimpleNamespace(
                handle_reply_edit=_AsyncRecorder(result=False),
                handle_reply_reminder=_AsyncRecorder(result=False),
            ),
            summary_handler=SimpleNamespace(
                handle_summary_intent=_AsyncRecorder(result=None),
                handle_hackathon_query=_AsyncRecorder(result=None),
            ),
            reminder_handler=SimpleNamespace(handle_text_reminder=_AsyncRecorder(result=True)),
        )

    async def test_non_personal_chat_is_ignored(self) -> None:
        parent = self._make_parent()
        router = TextInputRouter(parent)
        update = SimpleNamespace(
            message=SimpleNamespace(text="remind me tomorrow", reply_to_message=None),
            effective_user=SimpleNamespace(id=1),
            effective_chat=SimpleNamespace(id=99),
        )

        result = await router.handle_message(update, parse_add_payload=lambda _x: {}, build_group_summary=lambda: "")
        self.assertFalse(result)

    async def test_reply_edit_intent_short_circuits(self) -> None:
        parent = self._make_parent()
        parent.reply_handler.handle_reply_edit = _AsyncRecorder(result=True)
        router = TextInputRouter(parent)
        update = SimpleNamespace(
            message=SimpleNamespace(text="edit this", reply_to_message=object()),
            effective_user=SimpleNamespace(id=1),
            effective_chat=SimpleNamespace(id=7),
        )
        with _PatchIntents(
            has_edit_intent=lambda _t: True,
            has_summary_intent=lambda _t: False,
            has_hackathon_query_intent=lambda _t: False,
            has_reminder_intent=lambda _t: True,
        ):
            result = await router.handle_message(update, parse_add_payload=lambda _x: {}, build_group_summary=lambda: "")

        self.assertTrue(result)
        self.assertEqual(len(parent.reply_handler.handle_reply_edit.calls), 1)

    async def test_summary_intent_routes_to_summary_handler(self) -> None:
        parent = self._make_parent()
        router = TextInputRouter(parent)
        update = SimpleNamespace(
            message=SimpleNamespace(text="summarize this", reply_to_message=None),
            effective_user=SimpleNamespace(id=1),
            effective_chat=SimpleNamespace(id=7),
        )
        with _PatchIntents(
            has_edit_intent=lambda _t: False,
            has_summary_intent=lambda _t: True,
            has_hackathon_query_intent=lambda _t: False,
            has_reminder_intent=lambda _t: False,
        ):
            result = await router.handle_message(update, parse_add_payload=lambda _x: {}, build_group_summary=lambda: "")

        self.assertTrue(result)
        self.assertEqual(len(parent.summary_handler.handle_summary_intent.calls), 1)

    async def test_hackathon_intent_routes_to_hackathon_handler(self) -> None:
        parent = self._make_parent()
        router = TextInputRouter(parent)
        update = SimpleNamespace(
            message=SimpleNamespace(text="what hackathons are available", reply_to_message=None),
            effective_user=SimpleNamespace(id=1),
            effective_chat=SimpleNamespace(id=7),
        )
        with _PatchIntents(
            has_edit_intent=lambda _t: False,
            has_summary_intent=lambda _t: False,
            has_hackathon_query_intent=lambda _t: True,
            has_reminder_intent=lambda _t: False,
        ):
            result = await router.handle_message(update, parse_add_payload=lambda _x: {}, build_group_summary=lambda: "")

        self.assertTrue(result)
        self.assertEqual(len(parent.summary_handler.handle_hackathon_query.calls), 1)

    async def test_reminder_reply_path_preferred_before_plain_text(self) -> None:
        parent = self._make_parent()
        parent.reply_handler.handle_reply_reminder = _AsyncRecorder(result=True)
        router = TextInputRouter(parent)
        update = SimpleNamespace(
            message=SimpleNamespace(text="add as reminder", reply_to_message=object()),
            effective_user=SimpleNamespace(id=1),
            effective_chat=SimpleNamespace(id=7),
        )
        with _PatchIntents(
            has_edit_intent=lambda _t: False,
            has_summary_intent=lambda _t: False,
            has_hackathon_query_intent=lambda _t: False,
            has_reminder_intent=lambda _t: True,
        ):
            result = await router.handle_message(update, parse_add_payload=lambda _x: {}, build_group_summary=lambda: "")

        self.assertTrue(result)
        self.assertEqual(len(parent.reply_handler.handle_reply_reminder.calls), 1)
        self.assertEqual(len(parent.reminder_handler.handle_text_reminder.calls), 0)

    async def test_plain_reminder_path_calls_reminder_handler(self) -> None:
        parent = self._make_parent()
        router = TextInputRouter(parent)
        update = SimpleNamespace(
            message=SimpleNamespace(text="remind me tomorrow", reply_to_message=None),
            effective_user=SimpleNamespace(id=1),
            effective_chat=SimpleNamespace(id=7),
        )
        with _PatchIntents(
            has_edit_intent=lambda _t: False,
            has_summary_intent=lambda _t: False,
            has_hackathon_query_intent=lambda _t: False,
            has_reminder_intent=lambda _t: True,
        ):
            result = await router.handle_message(update, parse_add_payload=lambda _x: {}, build_group_summary=lambda: "")

        self.assertTrue(result)
        self.assertEqual(len(parent.reminder_handler.handle_text_reminder.calls), 1)


if __name__ == "__main__":
    unittest.main()
