from __future__ import annotations

import unittest
from types import SimpleNamespace

try:
    from src.app.handlers.list_sync_model_handler import ListSyncModelHandler
except Exception:  # pragma: no cover - optional runtime deps may be missing
    ListSyncModelHandler = None  # type: ignore[assignment]


class _FakeMessage:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def reply_text(self, text: str, reply_markup=None) -> None:  # noqa: ANN001 - test stub
        self.calls.append({"text": text, "reply_markup": reply_markup})


class _AsyncRecorder:
    def __init__(self, result=None) -> None:
        self.calls: list[tuple] = []
        self.result = result

    async def __call__(self, *args, **kwargs):  # noqa: ANN002,ANN003 - test stub
        self.calls.append((args, kwargs))
        return self.result


class ListSyncModelHandlerTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        if ListSyncModelHandler is None:
            self.skipTest("list/sync/model handler dependencies unavailable")

    async def test_list_command_without_args_shows_keyboard_prompt(self) -> None:
        message = _FakeMessage()
        bot = SimpleNamespace(_clear_pending_flows=lambda *_a, **_k: None)
        handler = ListSyncModelHandler(bot)
        update = SimpleNamespace(message=message, effective_chat=SimpleNamespace(id=10))
        context = SimpleNamespace(args=[])

        await handler.list_command(update, context)

        self.assertEqual(len(message.calls), 1)
        self.assertEqual(message.calls[0]["text"], "Choose list filter:")

    async def test_run_list_mode_unknown_replies_usage(self) -> None:
        message = _FakeMessage()
        bot = SimpleNamespace(db=SimpleNamespace(), _list_mode_in_local_timezone=lambda _m: [])
        handler = ListSyncModelHandler(bot)
        update = SimpleNamespace(message=message, callback_query=None, effective_chat=SimpleNamespace(id=10))

        await handler.run_list_mode(update, "invalid-mode")

        self.assertEqual(len(message.calls), 1)

    async def test_sync_command_disabled_replies_error(self) -> None:
        message = _FakeMessage()
        bot = SimpleNamespace(
            _clear_pending_flows=lambda *_a, **_k: None,
            calendar_sync=SimpleNamespace(is_enabled=lambda: False),
        )
        handler = ListSyncModelHandler(bot)
        update = SimpleNamespace(message=message, effective_user=SimpleNamespace(id=1), effective_chat=SimpleNamespace(id=10))
        context = SimpleNamespace(args=[])

        await handler.sync_command(update, context)

        self.assertEqual(len(message.calls), 1)

    async def test_model_wizard_role_then_name_sets_text_model(self) -> None:
        message = _FakeMessage()
        bot = SimpleNamespace(
            pending_model_wizards={10: {"step": "role"}},
            ollama=SimpleNamespace(
                list_models=lambda: ["m1"],
                set_text_model=lambda _name: None,
                set_vision_model=lambda _name: None,
            ),
            db=SimpleNamespace(set_app_setting=lambda *_a, **_k: None),
            vision_model_tags=set(),
            _save_vision_model_tags=lambda: None,
        )
        handler = ListSyncModelHandler(bot)
        update = SimpleNamespace(message=message, effective_chat=SimpleNamespace(id=10))

        handled_role = await handler.handle_pending_model_wizard(update, "text")
        self.assertTrue(handled_role)
        self.assertEqual(bot.pending_model_wizards[10]["step"], "name")

        handled_name = await handler.handle_pending_model_wizard(update, "m1")
        self.assertTrue(handled_name)
        self.assertNotIn(10, bot.pending_model_wizards)

    async def test_model_wizard_cancel_clears_state(self) -> None:
        message = _FakeMessage()
        bot = SimpleNamespace(
            pending_model_wizards={10: {"step": "role"}},
            ollama=SimpleNamespace(list_models=lambda: []),
            db=SimpleNamespace(set_app_setting=lambda *_a, **_k: None),
            vision_model_tags=set(),
            _save_vision_model_tags=lambda: None,
        )
        handler = ListSyncModelHandler(bot)
        update = SimpleNamespace(message=message, effective_chat=SimpleNamespace(id=10))

        handled = await handler.handle_pending_model_wizard(update, "cancel")

        self.assertTrue(handled)
        self.assertNotIn(10, bot.pending_model_wizards)
        self.assertEqual(message.calls[0]["text"], "Model wizard cancelled.")


if __name__ == "__main__":
    unittest.main()
