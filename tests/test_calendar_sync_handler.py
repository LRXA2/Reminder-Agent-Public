from __future__ import annotations

import unittest

from src.app.handlers.services.calendar_sync_handler import CalendarSyncHandler


class _FakeSettings:
    default_timezone = "UTC"


class _FakeBot:
    def __init__(self) -> None:
        self.settings = _FakeSettings()


class CalendarSyncHandlerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.handler = CalendarSyncHandler(_FakeBot())

    def test_extract_first_url_trims_trailing_punctuation(self) -> None:
        text = "details at https://example.com/test). and also https://ignored.example"
        self.assertEqual(self.handler.extract_first_url(text), "https://example.com/test")

    def test_clean_calendar_import_notes_removes_export_metadata(self) -> None:
        notes = "Reminder ID: 44\nPriority: high\nLink: https://x.y\nTopic: ops\n\nKeep this line\n"
        self.assertEqual(self.handler.clean_calendar_import_notes(notes), "Keep this line")

    def test_calendar_event_to_due_utc_with_datetime(self) -> None:
        event = {"start": {"dateTime": "2026-02-22T10:30:00-05:00"}}
        self.assertEqual(self.handler.calendar_event_to_due_utc(event), "2026-02-22T15:30:00+00:00")

    def test_calendar_event_to_due_utc_with_date_only(self) -> None:
        event = {"start": {"date": "2026-02-22"}}
        self.assertEqual(self.handler.calendar_event_to_due_utc(event), "2026-02-22T00:00:00+00:00")


if __name__ == "__main__":
    unittest.main()
