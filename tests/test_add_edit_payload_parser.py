from __future__ import annotations

import unittest
from types import SimpleNamespace

try:
    from src.app.handlers.commands.add_edit.parsing import AddEditPayloadParser
except Exception:  # pragma: no cover - optional runtime deps may be missing
    AddEditPayloadParser = None  # type: ignore[assignment]


class AddEditPayloadParserTests(unittest.TestCase):
    def setUp(self) -> None:
        if AddEditPayloadParser is None:
            self.skipTest("add/edit parser dependencies unavailable")
        bot = SimpleNamespace(
            settings=SimpleNamespace(default_timezone="UTC", datetime_parse_debug=False),
            reminder_logic_handler=SimpleNamespace(
                split_topics=lambda text: [p.strip() for p in text.split(",") if p.strip()],
            ),
            datetime_resolution_handler=SimpleNamespace(parse_natural_datetime=lambda _t: (None, "low")),
        )
        self.parser = AddEditPayloadParser(bot)  # type: ignore[arg-type]

    def test_parse_edit_payload_topic_add_and_priority(self) -> None:
        parsed = self.parser.parse_edit_payload("topic:+work,+ops priority:high")
        self.assertEqual(parsed["topic_mode"], "add")
        self.assertEqual(parsed["topic_values"], ["work", "ops"])
        self.assertEqual(parsed["priority"], "high")

    def test_parse_edit_payload_recurrence_none_clears(self) -> None:
        parsed = self.parser.parse_edit_payload("every:none")
        self.assertEqual(parsed["recurrence"], "")

    def test_parse_add_payload_requires_due_or_no_due_marker(self) -> None:
        parsed = self.parser.parse_add_payload("buy milk")
        self.assertIn("error", parsed)


if __name__ == "__main__":
    unittest.main()
