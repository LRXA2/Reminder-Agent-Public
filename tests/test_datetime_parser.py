from __future__ import annotations

import unittest
from datetime import datetime
from zoneinfo import ZoneInfo

try:
    from src.app.handlers.datetime_parser import parse_datetime_text
except Exception:  # pragma: no cover - optional runtime deps may be missing in this env
    parse_datetime_text = None  # type: ignore[assignment]


class DateTimeParserTests(unittest.TestCase):
    def setUp(self) -> None:
        if parse_datetime_text is None:
            self.skipTest("datetime parser dependencies unavailable")
        self.tz = ZoneInfo("Asia/Singapore")
        self.now_local = datetime(2026, 2, 21, 10, 0, tzinfo=self.tz)

    def test_tomorrow_with_time(self) -> None:
        assert parse_datetime_text is not None
        result = parse_datetime_text("tomorrow 9am", "Asia/Singapore", now_local=self.now_local)
        self.assertIsNotNone(result.dt)
        assert result.dt is not None
        self.assertEqual(result.dt.astimezone(self.tz).strftime("%Y-%m-%d %H:%M"), "2026-02-22 09:00")

    def test_next_friday_with_time(self) -> None:
        assert parse_datetime_text is not None
        result = parse_datetime_text("next friday 3pm", "Asia/Singapore", now_local=self.now_local)
        self.assertIsNotNone(result.dt)
        assert result.dt is not None
        self.assertEqual(result.dt.astimezone(self.tz).strftime("%Y-%m-%d %H:%M"), "2026-02-27 15:00")

    def test_explicit_date_with_time(self) -> None:
        assert parse_datetime_text is not None
        result = parse_datetime_text("28/02/26 11:30", "Asia/Singapore", now_local=self.now_local)
        self.assertIsNotNone(result.dt)
        assert result.dt is not None
        self.assertEqual(result.dt.astimezone(self.tz).strftime("%Y-%m-%d %H:%M"), "2026-02-28 11:30")

    def test_common_typo_tomorrow(self) -> None:
        assert parse_datetime_text is not None
        result = parse_datetime_text("tomrrow 9am", "Asia/Singapore", now_local=self.now_local)
        self.assertIsNotNone(result.dt)
        assert result.dt is not None
        self.assertEqual(result.dt.astimezone(self.tz).strftime("%Y-%m-%d %H:%M"), "2026-02-22 09:00")


if __name__ == "__main__":
    unittest.main()
