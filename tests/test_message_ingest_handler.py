from __future__ import annotations

import unittest
from types import SimpleNamespace

from src.app.handlers.runtime.message_ingest_handler import MessageIngestHandler


class MessageIngestHandlerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.bot = SimpleNamespace(settings=SimpleNamespace(monitored_group_chat_id=-1001, personal_chat_id=99))
        self.handler = MessageIngestHandler(self.bot)

    def test_should_store_message_for_monitored_group_only(self) -> None:
        self.assertTrue(self.handler.should_store_message(-1001, "group", "anything"))
        self.assertFalse(self.handler.should_store_message(-2002, "group", "anything"))

    def test_should_store_message_for_hackathon_dm_only(self) -> None:
        self.assertTrue(self.handler.should_store_message(99, "dm", "mlh registration deadline"))
        self.assertFalse(self.handler.should_store_message(99, "dm", "buy milk tomorrow"))
        self.assertFalse(self.handler.should_store_message(100, "dm", "mlh registration deadline"))


if __name__ == "__main__":
    unittest.main()
