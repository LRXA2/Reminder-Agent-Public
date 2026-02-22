from __future__ import annotations

import unittest
from datetime import datetime, timezone
from types import SimpleNamespace

from src.app.handlers.services.reminder_rules import ReminderLogicHandler


class ReminderRulesTests(unittest.TestCase):
    def setUp(self) -> None:
        bot = SimpleNamespace(
            db=SimpleNamespace(suggest_topics_for_chat=lambda _chat_id, _missing, limit=3: ["work", "urgent"]),
        )
        self.logic = ReminderLogicHandler(bot)

    def test_is_notes_list_candidate_rules(self) -> None:
        self.assertFalse(self.logic.is_notes_list_candidate({"notes": ""}))
        long_summary = {
            "notes": "x" * (self.logic.LONG_SUMMARY_NOTES_THRESHOLD + 1),
            "source_kind": "group_summary",
        }
        self.assertTrue(self.logic.is_notes_list_candidate(long_summary))
        edited_manual = {
            "notes": "quick note",
            "source_kind": "user_input",
            "created_at_utc": "2026-01-01T00:00:00+00:00",
            "updated_at_utc": "2026-01-01T01:00:00+00:00",
        }
        self.assertTrue(self.logic.is_notes_list_candidate(edited_manual))

    def test_split_topics_dedupes_case_insensitive(self) -> None:
        values = self.logic.split_topics("Work, work, Urgent,urgent, home")
        self.assertEqual(values, ["Work", "Urgent", "home"])

    def test_looks_like_inline_add_payload(self) -> None:
        self.assertTrue(self.logic.looks_like_inline_add_payload("remind me at: tomorrow 9am"))
        self.assertTrue(self.logic.looks_like_inline_add_payload("pay bill #finance"))
        self.assertTrue(self.logic.looks_like_inline_add_payload("call mom !high"))
        self.assertFalse(self.logic.looks_like_inline_add_payload("just normal sentence"))

    def test_compute_next_due_by_recurrence(self) -> None:
        daily = self.logic.compute_next_due("2026-03-10T09:00:00+00:00", "daily")
        weekly = self.logic.compute_next_due("2026-03-10T09:00:00+00:00", "weekly")
        monthly = self.logic.compute_next_due("2026-03-10T09:00:00+00:00", "monthly")
        self.assertEqual(daily, "2026-03-11T09:00:00+00:00")
        self.assertEqual(weekly, "2026-03-17T09:00:00+00:00")
        self.assertEqual(monthly, "2026-04-09T09:00:00+00:00")

    def test_compute_next_due_handles_invalid(self) -> None:
        self.assertIsNone(self.logic.compute_next_due("bad", "daily"))
        self.assertIsNone(self.logic.compute_next_due(datetime.now(timezone.utc).isoformat(), "yearly"))


if __name__ == "__main__":
    unittest.main()
