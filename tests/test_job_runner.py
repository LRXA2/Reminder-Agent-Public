from __future__ import annotations

import unittest
from types import SimpleNamespace

from src.app.handlers.job_runner import JobRunner


class _FakeBotSender:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def send_message(self, chat_id: int, text: str) -> None:
        self.calls.append({"chat_id": chat_id, "text": text})


class JobRunnerTests(unittest.IsolatedAsyncioTestCase):
    async def test_process_due_reminders_marks_notified_and_updates_recurrence(self) -> None:
        sender = _FakeBotSender()
        marked: list[int] = []
        updated_due: list[tuple[int, str]] = []
        sync_calls: list[int] = []

        db = SimpleNamespace(
            get_due_reminders=lambda _now_iso: [
                {
                    "id": 7,
                    "title": "Pay bill",
                    "priority": "high",
                    "chat_id_to_notify": 42,
                    "due_at_utc": "2026-03-10T09:00:00+00:00",
                    "recurrence_rule": "daily",
                }
            ],
            mark_reminder_notified=lambda rid, _due: marked.append(rid),
            update_recurring_due=lambda rid, due: updated_due.append((rid, due)),
        )
        bot = SimpleNamespace(
            db=db,
            app=SimpleNamespace(bot=sender),
            _compute_next_due=lambda _due, _recurrence: "2026-03-11T09:00:00+00:00",
            _sync_calendar_upsert=lambda rid: _async_append(sync_calls, rid),
        )
        runner = JobRunner(bot)

        await runner.process_due_reminders()

        self.assertEqual(len(sender.calls), 1)
        self.assertEqual(marked, [7])
        self.assertEqual(updated_due, [(7, "2026-03-11T09:00:00+00:00")])
        self.assertEqual(sync_calls, [7])

    async def test_build_group_summary_returns_empty_message_when_no_rows(self) -> None:
        bot = SimpleNamespace(
            settings=SimpleNamespace(monitored_group_chat_id=123),
            db=SimpleNamespace(fetch_recent_group_messages=lambda _cid, limit=50: []),
        )
        runner = JobRunner(bot)

        summary = await runner.build_group_summary(chat_id=123, save=False)
        self.assertIn("No recent messages found", summary)


async def _async_append(target: list[int], value: int) -> None:
    target.append(value)


if __name__ == "__main__":
    unittest.main()
