from __future__ import annotations

import unittest
from types import SimpleNamespace

try:
    from src.app.reminder_bot import ReminderBot
    from src.app.handlers.wizards import UiWizardHandler
except Exception:  # pragma: no cover - optional runtime deps may be missing in CI/dev env
    ReminderBot = None  # type: ignore[assignment]
    UiWizardHandler = None  # type: ignore[assignment]


class _FakeMessage:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def reply_text(self, text: str, reply_markup=None) -> None:  # noqa: ANN001 - test stub
        self.calls.append({"text": text, "reply_markup": reply_markup})


class NotesWizardCallbackTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        if ReminderBot is None or UiWizardHandler is None:
            self.skipTest("reminder bot dependencies unavailable")

        self.bot = object.__new__(ReminderBot)
        self.bot.settings = SimpleNamespace(default_timezone="UTC")
        self.bot.pending_notes_wizards = {}
        self.bot.ui_wizard_handler = UiWizardHandler(self.bot)
        self.bot.ui_wizard_handler._notes_wizard_keyboard = lambda: None
        self.bot.ui_wizard_handler.notes_wizard.collect_candidates = lambda _chat_id: [{"id": 12, "title": "Buy milk"}]

    async def test_notes_list_button_works_from_callback_update(self) -> None:
        target = _FakeMessage()
        update = SimpleNamespace(
            message=None,
            callback_query=SimpleNamespace(message=target),
            effective_chat=SimpleNamespace(id=1001),
        )

        self.bot.pending_notes_wizards[1001] = {"mode": "menu"}
        handled = await self.bot._handle_pending_notes_wizard(update, "list")

        self.assertTrue(handled)
        self.assertGreaterEqual(len(target.calls), 1)
        self.assertIn("Reminders with notes:", target.calls[0]["text"])
        self.assertIn("#12 Buy milk", target.calls[0]["text"])

    async def test_notes_cancel_button_works_from_callback_update(self) -> None:
        target = _FakeMessage()
        update = SimpleNamespace(
            message=None,
            callback_query=SimpleNamespace(message=target),
            effective_chat=SimpleNamespace(id=1002),
        )

        self.bot.pending_notes_wizards[1002] = {"mode": "menu"}
        handled = await self.bot._handle_pending_notes_wizard(update, "cancel")

        self.assertTrue(handled)
        self.assertNotIn(1002, self.bot.pending_notes_wizards)
        self.assertGreaterEqual(len(target.calls), 1)
        self.assertEqual(target.calls[0]["text"], "Notes flow cancelled.")


if __name__ == "__main__":
    unittest.main()
