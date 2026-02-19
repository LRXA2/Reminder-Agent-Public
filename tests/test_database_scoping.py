from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone

from src.storage.database import Database


class DatabaseScopingTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self._temp.close()
        self.db = Database(self._temp.name)
        self.user_id = self.db.upsert_user(telegram_user_id=111, username="tester", timezone_name="UTC")

    def tearDown(self) -> None:
        try:
            import os

            os.unlink(self._temp.name)
        except OSError:
            pass

    def test_chat_scoped_reminder_updates(self) -> None:
        due = (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat()
        reminder_id = self.db.create_reminder(
            user_id=self.user_id,
            source_message_id=None,
            source_kind="test",
            title="Scoped reminder",
            notes="notes",
            link="",
            priority="mid",
            due_at_utc=due,
            timezone_name="UTC",
            chat_id_to_notify=1001,
            recurrence_rule=None,
        )

        denied_update = self.db.update_reminder_fields_for_chat(
            reminder_id=reminder_id,
            chat_id_to_notify=2002,
            title="Should fail",
            notes="",
            link="",
            priority="high",
            due_at_utc=due,
            recurrence_rule=None,
        )
        self.assertFalse(denied_update)

        allowed_update = self.db.update_reminder_fields_for_chat(
            reminder_id=reminder_id,
            chat_id_to_notify=1001,
            title="Allowed",
            notes="",
            link="",
            priority="high",
            due_at_utc=due,
            recurrence_rule=None,
        )
        self.assertTrue(allowed_update)

        denied_done = self.db.mark_done_and_archive_for_chat(reminder_id, 2002)
        self.assertFalse(denied_done)
        allowed_done = self.db.mark_done_and_archive_for_chat(reminder_id, 1001)
        self.assertTrue(allowed_done)

    def test_recent_group_messages_since(self) -> None:
        now = datetime.now(timezone.utc)
        earlier = (now - timedelta(minutes=10)).isoformat()
        later = (now - timedelta(minutes=2)).isoformat()

        self.db.save_inbound_message(
            chat_id=-100111,
            telegram_message_id=1,
            sender_telegram_id=555,
            text="old message",
            chat_type="supergroup",
            source_type="group",
            received_at_utc=earlier,
        )
        self.db.save_inbound_message(
            chat_id=-100111,
            telegram_message_id=2,
            sender_telegram_id=555,
            text="new message",
            chat_type="supergroup",
            source_type="group",
            received_at_utc=later,
        )

        rows = self.db.fetch_recent_group_messages_since(-100111, (now - timedelta(minutes=5)).isoformat(), limit=50)
        self.assertEqual(len(rows), 1)
        self.assertEqual((rows[0]["text"] or "").strip(), "new message")

    def test_list_reminders_for_chat(self) -> None:
        due = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
        r1 = self.db.create_reminder(
            user_id=self.user_id,
            source_message_id=None,
            source_kind="test",
            title="chat a",
            notes="",
            link="",
            priority="mid",
            due_at_utc=due,
            timezone_name="UTC",
            chat_id_to_notify=101,
            recurrence_rule=None,
        )
        r2 = self.db.create_reminder(
            user_id=self.user_id,
            source_message_id=None,
            source_kind="test",
            title="chat b",
            notes="",
            link="",
            priority="mid",
            due_at_utc=due,
            timezone_name="UTC",
            chat_id_to_notify=202,
            recurrence_rule=None,
        )

        chat_a = self.db.list_reminders_for_chat(101)
        self.assertEqual(len(chat_a), 1)
        self.assertEqual(int(chat_a[0]["id"]), r1)

        chat_b = self.db.list_reminders_for_chat(202)
        self.assertEqual(len(chat_b), 1)
        self.assertEqual(int(chat_b[0]["id"]), r2)


if __name__ == "__main__":
    unittest.main()
