from __future__ import annotations

import asyncio
import logging
import threading
from datetime import timezone

from src.core.config import Settings
from src.storage.database import Database


LOGGER = logging.getLogger(__name__)


class UserbotIngestService:
    def __init__(self, settings: Settings, db: Database):
        self.settings = settings
        self.db = db
        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._client = None

    def start(self) -> None:
        if not self.settings.userbot_enabled:
            return
        if self._thread and self._thread.is_alive():
            return
        if not self.settings.userbot_api_id or not self.settings.userbot_api_hash:
            LOGGER.warning("Userbot enabled but USERBOT_API_ID/USERBOT_API_HASH missing; skipping userbot startup")
            return

        self._thread = threading.Thread(target=self._run_loop, name="userbot-ingest", daemon=True)
        self._thread.start()
        LOGGER.info("Userbot ingest service started")

    def _run_loop(self) -> None:
        try:
            self._loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self._loop)
            self._loop.run_until_complete(self._run_async())
        except Exception as exc:
            LOGGER.exception("Userbot ingest service stopped due to error: %s", exc)

    async def _run_async(self) -> None:
        try:
            from telethon import TelegramClient, events
        except Exception as exc:
            LOGGER.warning("Telethon not installed or failed to import: %s", exc)
            return

        ingest_chats = set(self.settings.userbot_ingest_chat_ids)
        chats_filter = list(ingest_chats) if ingest_chats else None

        client = TelegramClient(
            self.settings.userbot_session_name,
            self.settings.userbot_api_id,
            self.settings.userbot_api_hash,
        )
        self._client = client

        @client.on(events.NewMessage(chats=chats_filter))
        async def on_new_message(event) -> None:  # type: ignore[no-untyped-def]
            message = event.message
            if message is None:
                return

            chat_id = int(event.chat_id) if event.chat_id is not None else 0
            if ingest_chats and chat_id not in ingest_chats:
                return

            text = (message.message or "").strip()
            if not text:
                return

            msg_date = message.date
            if msg_date.tzinfo is None:
                msg_date = msg_date.replace(tzinfo=timezone.utc)

            sender_id = int(message.sender_id) if message.sender_id is not None else None
            if event.is_group:
                chat_type = "group"
                source_type = "group"
            elif event.is_channel:
                chat_type = "channel"
                source_type = "group"
            else:
                chat_type = "private"
                source_type = "dm"

            self.db.save_inbound_message(
                chat_id=chat_id,
                telegram_message_id=int(message.id),
                sender_telegram_id=sender_id,
                text=text,
                chat_type=chat_type,
                source_type=source_type,
                received_at_utc=msg_date.astimezone(timezone.utc).isoformat(),
            )

        await client.start()
        LOGGER.info("Userbot client connected as account session '%s'", self.settings.userbot_session_name)
        await client.run_until_disconnected()

    async def send_message_if_allowed(self, chat_id: int, text: str) -> bool:
        if not self.settings.userbot_allow_sending:
            LOGGER.info("Userbot sending blocked (USERBOT_ALLOW_SENDING=false)")
            return False

        allowed = set(self.settings.userbot_send_whitelist_chat_ids)
        if allowed and int(chat_id) not in allowed:
            LOGGER.info("Userbot sending blocked for chat_id=%s (not in USERBOT_SEND_WHITELIST_CHAT_IDS)", chat_id)
            return False

        if self._client is None:
            LOGGER.warning("Userbot sending requested but client is not connected")
            return False

        await self._client.send_message(chat_id, text)
        return True
