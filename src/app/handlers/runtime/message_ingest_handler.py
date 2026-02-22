from __future__ import annotations

from datetime import timezone
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from telegram import Update
    from telegram.ext import ContextTypes

    from src.app.bot_orchestrator import ReminderBot


class MessageIngestHandler:
    def __init__(self, bot: "ReminderBot") -> None:
        self.bot = bot

    async def ingest_message(self, update: "Update", context: "ContextTypes.DEFAULT_TYPE") -> None:
        del context
        if not update.message:
            return
        message = update.message
        text = message.text or message.caption or ""
        source_type = "group" if message.chat.type in {"group", "supergroup"} else "dm"
        if not self.should_store_message(message.chat_id, source_type, text):
            return
        received_at = message.date.astimezone(timezone.utc).isoformat()
        sender_id = message.from_user.id if message.from_user else None
        self.bot.db.save_inbound_message(
            chat_id=message.chat_id,
            telegram_message_id=message.message_id,
            sender_telegram_id=sender_id,
            text=text,
            chat_type=message.chat.type,
            source_type=source_type,
            received_at_utc=received_at,
        )

    def should_store_message(self, chat_id: int, source_type: str, text: str) -> bool:
        normalized = (text or "").strip().lower()

        if source_type == "group":
            monitored_group = self.bot.settings.monitored_group_chat_id
            if not monitored_group:
                return False
            return int(chat_id) == int(monitored_group)

        if int(chat_id) != int(self.bot.settings.personal_chat_id):
            return False

        hackathon_markers = ("hackathon", "hackathons", "devpost", "mlh", "registration", "deadline")
        return any(marker in normalized for marker in hackathon_markers)
