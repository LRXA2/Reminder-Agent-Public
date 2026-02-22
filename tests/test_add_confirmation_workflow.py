from __future__ import annotations

import unittest
from types import SimpleNamespace

try:
    from src.app.handlers.commands.add_edit.confirmation_workflow import AddConfirmationWorkflow
except Exception:  # pragma: no cover - optional runtime deps may be missing
    AddConfirmationWorkflow = None  # type: ignore[assignment]


class _FakeMessage:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def reply_text(self, text: str, reply_markup=None) -> None:  # noqa: ANN001
        del reply_markup
        self.calls.append(text)


class AddConfirmationWorkflowTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        if AddConfirmationWorkflow is None:
            self.skipTest("confirmation workflow dependencies unavailable")

    async def test_cancel_removes_pending_confirmation(self) -> None:
        message = _FakeMessage()
        bot = SimpleNamespace(
            pending_add_confirmations={10: [{"title": "x", "priority": "mid", "due_at_utc": "", "recurrence": ""}]},
            settings=SimpleNamespace(default_timezone="UTC"),
        )
        workflow = AddConfirmationWorkflow(bot)  # type: ignore[arg-type]
        update = SimpleNamespace(message=message, effective_user=SimpleNamespace(id=1), effective_chat=SimpleNamespace(id=10))

        handled = await workflow.handle_pending_add_confirmation(update, "cancel")

        self.assertTrue(handled)
        self.assertNotIn(10, bot.pending_add_confirmations)

    async def test_yes_creates_reminder_and_syncs(self) -> None:
        message = _FakeMessage()
        created: list[int] = []
        synced: list[int] = []
        bot = SimpleNamespace(
            pending_add_confirmations={
                10: [
                    {
                        "title": "Pay rent",
                        "topic": "home",
                        "priority": "high",
                        "due_at_utc": "2026-03-01T09:00:00+00:00",
                        "recurrence": "",
                        "link": "",
                    }
                ]
            },
            settings=SimpleNamespace(default_timezone="UTC"),
            reminder_logic_handler=SimpleNamespace(split_topics=lambda _t: ["home"]),
            db=SimpleNamespace(
                upsert_user=lambda *_a, **_k: 7,
                create_reminder=lambda **_k: 55,
                set_reminder_topics_for_chat=lambda rid, _cid, _topics: created.append(rid),
            ),
            calendar_sync_handler=SimpleNamespace(sync_calendar_upsert=lambda rid: _async_append(synced, rid)),
        )
        workflow = AddConfirmationWorkflow(bot)  # type: ignore[arg-type]
        update = SimpleNamespace(
            message=message,
            effective_user=SimpleNamespace(id=1, username="u"),
            effective_chat=SimpleNamespace(id=10),
        )

        handled = await workflow.handle_pending_add_confirmation(update, "yes")

        self.assertTrue(handled)
        self.assertEqual(created, [55])
        self.assertEqual(synced, [55])
        self.assertNotIn(10, bot.pending_add_confirmations)


async def _async_append(target: list[int], value: int) -> None:
    target.append(value)


if __name__ == "__main__":
    unittest.main()
