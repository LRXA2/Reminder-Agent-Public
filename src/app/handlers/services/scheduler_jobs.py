from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING

from src.app.handlers.reminder_formatting import format_reminder_list_item

if TYPE_CHECKING:
    from src.app.bot_orchestrator import ReminderBot


LOGGER = logging.getLogger(__name__)


class JobRunner:
    def __init__(self, bot: "ReminderBot") -> None:
        self.bot = bot

    async def process_due_reminders(self) -> None:
        now_iso = datetime.now(timezone.utc).isoformat()
        rows = self.bot.db.get_due_reminders(now_iso)
        for row in rows:
            chat_id = int(row["chat_id_to_notify"])
            try:
                await self.bot.app.bot.send_message(
                    chat_id=chat_id,
                    text=f"Reminder #{row['id']}: {row['title']} ({row['priority']})",
                )
            except Exception as exc:
                LOGGER.exception("Failed to send reminder %s: %s", row["id"], exc)
                continue
            self.bot.db.mark_reminder_notified(int(row["id"]), row["due_at_utc"])

            recurrence = (row["recurrence_rule"] or "").strip().lower()
            if recurrence:
                next_due = self.bot.reminder_logic_handler.compute_next_due(row["due_at_utc"], recurrence)
                if next_due:
                    self.bot.db.update_recurring_due(int(row["id"]), next_due)
                    await self.bot.calendar_sync_handler.sync_calendar_upsert(int(row["id"]))

    async def cleanup_archives(self) -> None:
        deleted = self.bot.db.delete_old_archived(self.bot.settings.archive_retention_days)
        if deleted:
            LOGGER.info("Deleted %s archived reminders older than retention", deleted)

    async def cleanup_messages(self) -> None:
        retention_days = self.bot.settings.message_retention_days
        if retention_days <= 0:
            return
        deleted = self.bot.db.delete_old_messages(retention_days)
        if deleted:
            LOGGER.info("Deleted %s stored messages older than %s days", deleted, retention_days)
        tombstones_deleted = self.bot.db.cleanup_calendar_tombstones(ttl_days=30)
        if tombstones_deleted:
            LOGGER.info("Deleted %s expired calendar tombstones", tombstones_deleted)

    async def process_auto_summaries(self) -> None:
        if not self.bot.settings.auto_summary_enabled:
            return
        if not self.bot.settings.personal_chat_id:
            return

        configured_chat_ids = list(self.bot.settings.auto_summary_chat_ids)
        if not configured_chat_ids and self.bot.settings.monitored_group_chat_id:
            configured_chat_ids = [int(self.bot.settings.monitored_group_chat_id)]
        if not configured_chat_ids and self.bot.settings.userbot_ingest_chat_ids:
            configured_chat_ids = [int(chat_id) for chat_id in self.bot.settings.userbot_ingest_chat_ids]
        if not configured_chat_ids:
            return

        now = datetime.now(timezone.utc)
        min_interval = timedelta(minutes=self.bot.settings.auto_summary_min_interval_minutes)

        for chat_id in configured_chat_ids:
            setting_key = f"auto_summary_last_sent_{chat_id}"
            raw_last = self.bot.db.get_app_setting(setting_key)
            if raw_last:
                try:
                    last_sent = datetime.fromisoformat(raw_last)
                    if last_sent.tzinfo is None:
                        last_sent = last_sent.replace(tzinfo=timezone.utc)
                except ValueError:
                    last_sent = now - timedelta(days=3650)
            else:
                last_sent = now - timedelta(days=3650)

            if now - last_sent < min_interval:
                continue
            new_rows = self.bot.db.fetch_recent_group_messages_since(
                group_chat_id=chat_id,
                since_utc_iso=last_sent.astimezone(timezone.utc).isoformat(),
                limit=200,
            )
            if not new_rows:
                continue

            summary = await self.summarize_group_rows(new_rows)
            if summary.startswith("No recent messages found for chat"):
                continue

            self.bot.db.save_summary(
                group_chat_id=chat_id,
                window_start_utc=last_sent.astimezone(timezone.utc).isoformat(),
                window_end_utc=now.isoformat(),
                summary_text=summary,
            )

            await self.bot.app.bot.send_message(
                chat_id=self.bot.settings.personal_chat_id,
                text=f"Auto summary for {chat_id}:\n\n{summary}",
            )
            self.bot.db.set_app_setting(setting_key, now.isoformat())

    async def send_daily_digest(self) -> None:
        if not self.bot.settings.personal_chat_id:
            return
        lines = ["Daily digest"]
        all_items = self.bot.db.list_reminders("all")
        if all_items:
            lines.append("All open reminders:")
            for idx, row in enumerate(all_items[:20], start=1):
                lines.append(format_reminder_list_item(idx, dict(row), self.bot.settings.default_timezone))
            if len(all_items) > 20:
                lines.append(f"...and {len(all_items) - 20} more.")
        else:
            lines.append("All open reminders: none")

        if self.bot.settings.monitored_group_chat_id:
            summary = await self.build_group_summary(chat_id=self.bot.settings.monitored_group_chat_id, save=False)
            lines.append("Group summary:")
            lines.append(summary)

        await self.bot.app.bot.send_message(chat_id=self.bot.settings.personal_chat_id, text="\n".join(lines))

    async def build_group_summary(self, chat_id: int | None = None, save: bool = True) -> str:
        target_chat_id = int(chat_id) if chat_id is not None else int(self.bot.settings.monitored_group_chat_id)
        rows = self.bot.db.fetch_recent_group_messages(target_chat_id, limit=50)
        if not rows:
            return f"No recent messages found for chat {target_chat_id}."

        summary = await self.summarize_group_rows(rows)
        if save:
            now = datetime.now(timezone.utc)
            window_start = (now - timedelta(hours=24)).isoformat()
            self.bot.db.save_summary(
                target_chat_id,
                window_start,
                now.isoformat(),
                summary,
            )
        return summary

    async def summarize_group_rows(self, rows: list) -> str:
        if not rows:
            return "No recent messages found for chat."

        lines = []
        for row in reversed(rows):
            text = (row["text"] or "").strip()
            if not text:
                continue
            if len(text) > 500:
                text = text[:500] + "..."
            lines.append(f"[{row['received_at_utc']}] {text}")
        if not lines:
            return "No recent messages found for chat."
        return await self.bot.run_gpu_task(self.bot.ollama.summarize_messages, lines)
