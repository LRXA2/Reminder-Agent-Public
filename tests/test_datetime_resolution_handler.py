from __future__ import annotations

import unittest
from datetime import datetime, timezone
from types import SimpleNamespace

try:
    from src.app.handlers.services.datetime_resolution_handler import DateTimeResolutionHandler
except Exception:  # pragma: no cover - optional runtime deps may be missing
    DateTimeResolutionHandler = None  # type: ignore[assignment]


class DateTimeResolutionHandlerTests(unittest.TestCase):
    def setUp(self) -> None:
        if DateTimeResolutionHandler is None:
            self.skipTest("datetime resolution handler dependencies unavailable")
        fake_bot = SimpleNamespace(
            settings=SimpleNamespace(default_timezone="UTC", datetime_parse_debug=False),
            ollama=SimpleNamespace(generate_text=lambda _prompt: ""),
        )
        self.handler = DateTimeResolutionHandler(fake_bot)  # type: ignore[arg-type]

    def test_has_explicit_time_detects_time_tokens(self) -> None:
        self.assertTrue(self.handler.has_explicit_time("tomorrow at 9:30"))
        self.assertTrue(self.handler.has_explicit_time("next monday 7pm"))
        self.assertFalse(self.handler.has_explicit_time("next monday"))

    def test_normalize_all_day_datetime_sets_midnight_when_no_time(self) -> None:
        parsed = datetime(2026, 2, 25, 14, 45, tzinfo=timezone.utc)
        normalized = self.handler.normalize_all_day_datetime(parsed, "next wednesday")
        self.assertEqual(normalized.hour, 0)
        self.assertEqual(normalized.minute, 0)
        self.assertEqual(normalized.second, 0)

    def test_parse_json_object_extracts_embedded_json(self) -> None:
        raw = "model says: {\"due_text\":\"tomorrow\",\"confidence\":\"high\"} done"
        parsed = self.handler.parse_json_object(raw)
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed.get("due_text"), "tomorrow")


if __name__ == "__main__":
    unittest.main()
