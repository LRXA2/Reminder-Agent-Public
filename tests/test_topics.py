from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone

from src.storage.database import Database


class TopicWorkflowTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self._temp.close()
        self.db = Database(self._temp.name)
        self.chat_id = 101
        self.user_id = self.db.upsert_user(telegram_user_id=111, username="tester", timezone_name="UTC")

    def tearDown(self) -> None:
        try:
            import os

            os.unlink(self._temp.name)
        except OSError:
            pass

    def _create_reminder(self, title: str) -> int:
        due = (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat()
        return self.db.create_reminder(
            user_id=self.user_id,
            source_message_id=None,
            source_kind="test",
            title=title,
            topic="",
            notes="",
            link="",
            priority="mid",
            due_at_utc=due,
            timezone_name="UTC",
            chat_id_to_notify=self.chat_id,
            recurrence_rule=None,
        )

    def test_create_topic_same_name_indexes_internal(self) -> None:
        self.db.create_topic_for_chat(self.chat_id, "school")
        self.db.create_topic_for_chat(self.chat_id, "school")
        rows = self.db.list_topic_index_for_chat(self.chat_id, include_archived=True)
        internals = [str(row["internal_name"]) for row in rows]
        displays = [str(row["display_name"]) for row in rows]
        self.assertIn("school", internals)
        self.assertIn("school(1)", internals)
        self.assertEqual(displays.count("school"), 2)

    def test_set_add_remove_topics_for_single_reminder(self) -> None:
        self.db.create_topic_for_chat(self.chat_id, "school")
        self.db.create_topic_for_chat(self.chat_id, "work")
        reminder_id = self._create_reminder("Task")

        ok_set = self.db.set_reminder_topics_for_chat(reminder_id, self.chat_id, ["school"])
        self.assertTrue(ok_set)
        row = self.db.get_reminder_by_id_for_chat(reminder_id, self.chat_id)
        self.assertEqual(str(row["topics_text"]), "school")

        ok_add = self.db.add_topic_to_reminder_for_chat(reminder_id, self.chat_id, "work")
        self.assertTrue(ok_add)
        row = self.db.get_reminder_by_id_for_chat(reminder_id, self.chat_id)
        self.assertIn("school", str(row["topics_text"]))
        self.assertIn("work", str(row["topics_text"]))

        ok_remove = self.db.remove_one_topic_from_reminder_for_chat(reminder_id, self.chat_id, "school")
        self.assertTrue(ok_remove)
        row = self.db.get_reminder_by_id_for_chat(reminder_id, self.chat_id)
        self.assertNotIn("school", str(row["topics_text"]))
        self.assertIn("work", str(row["topics_text"]))

    def test_merge_topics(self) -> None:
        from_id = self.db.create_topic_for_chat(self.chat_id, "schol")
        to_id = self.db.create_topic_for_chat(self.chat_id, "school")
        reminder_id = self._create_reminder("Merge me")
        self.db.add_topic_to_reminder_for_chat(reminder_id, self.chat_id, "schol")

        merged = self.db.merge_topics_for_chat(self.chat_id, from_id, to_id)
        self.assertTrue(merged)

        row = self.db.get_reminder_by_id_for_chat(reminder_id, self.chat_id)
        self.assertIn("school", str(row["topics_text"]))
        self.assertNotIn("schol", str(row["topics_text"]))

    def test_missing_topics_and_suggestions(self) -> None:
        self.db.create_topic_for_chat(self.chat_id, "school")
        missing = self.db.has_missing_topics_for_chat(self.chat_id, ["scool", "school"])
        self.assertEqual(missing, ["scool"])
        suggestions = self.db.suggest_topics_for_chat(self.chat_id, "scho")
        self.assertIn("school", suggestions)


if __name__ == "__main__":
    unittest.main()
