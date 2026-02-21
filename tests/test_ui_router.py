from __future__ import annotations

import unittest
from types import SimpleNamespace

try:
    from src.app.handlers.wizards.ui_router import UiRouter
except Exception:  # pragma: no cover - optional runtime deps may be missing
    UiRouter = None  # type: ignore[assignment]


class _FakeMessage:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def reply_text(self, text: str, reply_markup=None) -> None:  # noqa: ANN001 - test stub
        self.calls.append({"text": text, "reply_markup": reply_markup})


class _FakeQuery:
    def __init__(self, data: str, user_id: int = 1) -> None:
        self.data = data
        self.message = _FakeMessage()
        self.from_user = SimpleNamespace(id=user_id)
        self.answers: list[dict] = []

    async def answer(self, text: str | None = None, show_alert: bool = False) -> None:
        self.answers.append({"text": text, "show_alert": show_alert})


class _AwaitableCall:
    def __init__(self) -> None:
        self.calls: list[tuple] = []

    async def __call__(self, *args, **kwargs):  # noqa: ANN002,ANN003 - test stub
        self.calls.append((args, kwargs))
        return None


class UiRouterTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        if UiRouter is None:
            self.skipTest("ui router dependencies unavailable")

    async def test_unauthorized_user_gets_alert(self) -> None:
        bot = SimpleNamespace(settings=SimpleNamespace(allowed_telegram_user_ids=[2]))
        ui = SimpleNamespace(bot=bot)
        router = UiRouter(ui)
        query = _FakeQuery("ui:list:all", user_id=1)
        update = SimpleNamespace(callback_query=query, effective_chat=SimpleNamespace(id=100))

        await router.handle(update, SimpleNamespace())

        self.assertEqual(len(query.answers), 1)
        self.assertEqual(query.answers[0]["text"], "Not authorized")
        self.assertTrue(query.answers[0]["show_alert"])

    async def test_notes_list_callback_dispatches_notes_wizard(self) -> None:
        notes_handler = _AwaitableCall()
        bot = SimpleNamespace(settings=SimpleNamespace(allowed_telegram_user_ids=[]))
        ui = SimpleNamespace(
            bot=bot,
            _handle_pending_notes_wizard=notes_handler,
            _notes_wizard_keyboard=lambda: None,
        )
        router = UiRouter(ui)
        query = _FakeQuery("ui:notes:list")
        update = SimpleNamespace(callback_query=query, effective_chat=SimpleNamespace(id=100))

        await router.handle(update, SimpleNamespace())

        self.assertEqual(len(notes_handler.calls), 1)
        self.assertEqual(notes_handler.calls[0][0][1], "list")

    async def test_topics_list_all_maps_to_list_all_text(self) -> None:
        topics_handler = _AwaitableCall()
        bot = SimpleNamespace(settings=SimpleNamespace(allowed_telegram_user_ids=[]))
        ui = SimpleNamespace(bot=bot, _handle_pending_topics_wizard=topics_handler, _topics_wizard_keyboard=lambda: None)
        router = UiRouter(ui)
        query = _FakeQuery("ui:topics:list_all")
        update = SimpleNamespace(callback_query=query, effective_chat=SimpleNamespace(id=100))

        await router.handle(update, SimpleNamespace())

        self.assertEqual(topics_handler.calls[0][0][1], "list all")

    async def test_delete_confirm_maps_to_yes(self) -> None:
        delete_handler = _AwaitableCall()
        bot = SimpleNamespace(settings=SimpleNamespace(allowed_telegram_user_ids=[]))
        ui = SimpleNamespace(bot=bot, _handle_pending_delete_wizard=delete_handler, _delete_wizard_keyboard=lambda: None)
        router = UiRouter(ui)
        query = _FakeQuery("ui:delete:confirm")
        update = SimpleNamespace(callback_query=query, effective_chat=SimpleNamespace(id=100))

        await router.handle(update, SimpleNamespace())

        self.assertEqual(delete_handler.calls[0][0][1], "yes")

    async def test_unknown_edit_action_replies_error(self) -> None:
        bot = SimpleNamespace(settings=SimpleNamespace(allowed_telegram_user_ids=[]))
        ui = SimpleNamespace(bot=bot, _handle_pending_edit_wizard=_AwaitableCall())
        router = UiRouter(ui)
        query = _FakeQuery("ui:edit:bogus")
        update = SimpleNamespace(callback_query=query, effective_chat=SimpleNamespace(id=100))

        await router.handle(update, SimpleNamespace())

        self.assertEqual(query.message.calls[0]["text"], "Unknown edit action.")


if __name__ == "__main__":
    unittest.main()
