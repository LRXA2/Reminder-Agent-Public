from __future__ import annotations

import unittest
from types import SimpleNamespace

try:
    from src.app.handlers.reminder_draft import ReminderDraftManager
except Exception:  # pragma: no cover - optional runtime deps may be missing in CI/dev env
    ReminderDraftManager = None  # type: ignore[assignment]


class _StubOllama:
    def generate_text(self, _prompt: str) -> str:
        return "{}"


class DraftSchemaTests(unittest.TestCase):
    def setUp(self) -> None:
        if ReminderDraftManager is None:
            self.skipTest("reminder draft manager dependencies unavailable")
        self.manager = ReminderDraftManager(
            db=SimpleNamespace(),
            ollama=_StubOllama(),
            settings=SimpleNamespace(default_timezone="UTC"),
            run_gpu_task=lambda func, *args, **kwargs: func(*args, **kwargs),
        )

    def test_normalize_v1_payload_defaults_v2_fields(self) -> None:
        payload = {
            "appropriate": True,
            "reason": "ok",
            "reminders": [
                {
                    "title": "Submit form",
                    "notes": "Complete registration",
                    "priority": "high",
                    "due_text": "2026-03-16 09:00",
                }
            ],
        }
        normalized = self.manager._normalize_payload(payload)
        self.assertEqual(normalized["schema_version"], "1")
        self.assertEqual(len(normalized["reminders"]), 1)
        r0 = normalized["reminders"][0]
        self.assertEqual(r0["due_mode"], "datetime")
        self.assertEqual(r0["confidence"], "medium")
        self.assertEqual(r0["topics"], [])

    def test_normalize_v2_payload_sanitizes_enums(self) -> None:
        payload = {
            "schema_version": "2",
            "appropriate": True,
            "reason": "ok",
            "reminders": [
                {
                    "title": "A" * 200,
                    "notes": "B" * 500,
                    "priority": "urgent",
                    "due_mode": "sometime",
                    "due_text": "next monday",
                    "confidence": "sure",
                    "topics": ["school", "work", "fitness", "extra"],
                }
            ],
        }
        normalized = self.manager._normalize_payload(payload)
        r0 = normalized["reminders"][0]
        self.assertEqual(normalized["schema_version"], "2")
        self.assertEqual(len(r0["title"]), 120)
        self.assertEqual(len(r0["notes"]), 280)
        self.assertEqual(r0["priority"], "mid")
        self.assertEqual(r0["due_mode"], "datetime")
        self.assertEqual(r0["confidence"], "medium")
        self.assertEqual(len(r0["topics"]), 3)

    def test_due_mode_all_day_normalizes_to_midnight(self) -> None:
        due = self.manager._parse_due_to_utc("2026-03-16", due_mode="all_day")
        self.assertTrue(due.endswith("+00:00"))
        self.assertIn("T00:00:00", due)


if __name__ == "__main__":
    unittest.main()
