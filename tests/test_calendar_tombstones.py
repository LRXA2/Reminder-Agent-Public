from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone

from src.storage.database import Database


class CalendarTombstoneTests(unittest.TestCase):
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

    def test_tombstone_roundtrip(self) -> None:
        event_id = "abc123"
        self.db.add_calendar_event_tombstone(event_id, provider="google")
        self.assertTrue(self.db.is_calendar_event_tombstoned(event_id, provider="google", ttl_days=30))

    def test_tombstone_cleanup(self) -> None:
        event_id = "expired-event"
        self.db.add_calendar_event_tombstone(event_id, provider="google")
        old_ts = (datetime.now(timezone.utc) - timedelta(days=40)).isoformat()
        self.db._execute(
            "UPDATE calendar_sync_tombstones SET deleted_at_utc = ? WHERE provider = ? AND event_id = ?",
            (old_ts, "google", event_id),
        )
        deleted = self.db.cleanup_calendar_tombstones(ttl_days=30)
        self.assertGreaterEqual(deleted, 1)
        self.assertFalse(self.db.is_calendar_event_tombstoned(event_id, provider="google", ttl_days=30))


if __name__ == "__main__":
    unittest.main()
