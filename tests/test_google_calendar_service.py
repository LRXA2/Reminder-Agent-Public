from __future__ import annotations

import unittest
from types import SimpleNamespace

from src.integrations.google_calendar_service import GoogleCalendarSyncService


class GoogleCalendarServiceTests(unittest.TestCase):
    def _make_service(self) -> GoogleCalendarSyncService:
        settings = SimpleNamespace(
            gcal_sync_enabled=True,
            gcal_calendar_id="primary",
            gcal_sync_from_calendar_ids=("primary", "team@example.com"),
            gcal_credentials_file="",
            default_timezone="UTC",
        )
        db = SimpleNamespace()
        return GoogleCalendarSyncService(settings, db)

    def test_event_ref_roundtrip(self) -> None:
        service = self._make_service()
        ref = service.make_event_ref("team@example.com", "abc123")
        self.assertEqual(ref, "team@example.com::abc123")
        self.assertEqual(service.parse_event_ref(ref), ("team@example.com", "abc123"))

    def test_parse_legacy_event_ref_without_calendar(self) -> None:
        service = self._make_service()
        self.assertEqual(service.parse_event_ref("abc123"), ("", "abc123"))


if __name__ == "__main__":
    unittest.main()
