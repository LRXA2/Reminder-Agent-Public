from __future__ import annotations

import tempfile
import unittest

from src.storage.database import Database


class GmailDatabaseTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self._temp.close()
        self.db = Database(self._temp.name)

    def tearDown(self) -> None:
        try:
            import os

            os.unlink(self._temp.name)
        except OSError:
            pass

    def test_save_gmail_message_and_dedupe_by_account_message(self) -> None:
        self.db.save_gmail_processed_message(
            account_id="work",
            gmail_message_id="abc",
            thread_id="thr",
            from_email="x@y.com",
            subject="Subj",
            snippet="Snippet",
            internal_date_utc="2026-01-01T00:00:00+00:00",
            label_ids=["INBOX"],
            importance_score=0.8,
            importance_reason="rule",
            is_important=True,
            summary_text="summary",
            notified=False,
        )
        self.assertTrue(self.db.is_gmail_message_processed("work", "abc"))

        self.db.save_gmail_processed_message(
            account_id="work",
            gmail_message_id="abc",
            thread_id="thr2",
            from_email="x@y.com",
            subject="Subj2",
            snippet="Snippet2",
            internal_date_utc="2026-01-01T00:10:00+00:00",
            label_ids=["INBOX", "IMPORTANT"],
            importance_score=0.9,
            importance_reason="updated",
            is_important=True,
            summary_text="summary2",
            notified=True,
        )
        rows = self.db.list_recent_gmail_events("work", limit=10)
        self.assertEqual(len(rows), 1)
        self.assertEqual(str(rows[0]["subject"]), "Subj2")

    def test_unnotified_important_query_and_mark_notified(self) -> None:
        self.db.save_gmail_processed_message(
            account_id="work",
            gmail_message_id="n1",
            thread_id="thr",
            from_email="x@y.com",
            subject="Need action",
            snippet="Please review",
            internal_date_utc="2026-01-01T00:00:00+00:00",
            label_ids=["INBOX"],
            importance_score=0.9,
            importance_reason="rule",
            is_important=True,
            summary_text="summary",
            notified=False,
        )
        pending = self.db.list_unnotified_important_gmail_events("work", limit=10)
        self.assertEqual(len(pending), 1)
        self.db.mark_gmail_notified("work", "n1")
        pending_after = self.db.list_unnotified_important_gmail_events("work", limit=10)
        self.assertEqual(len(pending_after), 0)

    def test_account_state_upsert_roundtrip(self) -> None:
        self.db.upsert_gmail_account_state("work", "2026-01-01T00:00:00+00:00", "", "42")
        row = self.db.get_gmail_account_state("work")
        self.assertIsNotNone(row)
        assert row is not None
        self.assertEqual(str(row["last_history_id"]), "42")

    def test_recent_notified_thread_lookup(self) -> None:
        self.db.save_gmail_processed_message(
            account_id="work",
            gmail_message_id="thread-a-1",
            thread_id="thread-a",
            from_email="x@y.com",
            subject="One",
            snippet="Snippet",
            internal_date_utc="2026-01-01T00:00:00+00:00",
            label_ids=["INBOX"],
            importance_score=0.8,
            importance_reason="rule",
            is_important=True,
            summary_text="summary",
            notified=True,
        )
        found = self.db.has_recent_notified_gmail_thread("work", "thread-a", "2000-01-01T00:00:00+00:00")
        self.assertTrue(found)


if __name__ == "__main__":
    unittest.main()
