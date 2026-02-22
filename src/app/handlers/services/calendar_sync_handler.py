from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime, timezone
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

if TYPE_CHECKING:
    from telegram import Update

    from src.app.bot_orchestrator import ReminderBot


LOGGER = logging.getLogger(__name__)


class CalendarSyncHandler:
    def __init__(self, bot: "ReminderBot") -> None:
        self.bot = bot

    async def sync_to_google_calendar(self, update: Update) -> tuple[int, int, list[tuple[int, str]]]:
        push_total = 0
        push_ok = 0
        failures: list[tuple[int, str]] = []
        rows = self.bot.db.list_reminders_for_chat(update.effective_chat.id)
        for row in rows:
            reminder_id = int(row["id"])
            push_total += 1
            ok = await asyncio.to_thread(self.bot.calendar_sync.upsert_for_reminder_id, reminder_id)
            if ok:
                push_ok += 1
            else:
                reason = self.bot.calendar_sync.get_last_error() or "unknown error"
                failures.append((reminder_id, reason))
        return push_total, push_ok, failures

    async def sync_from_google_calendar(self, update: Update, allow_update_existing: bool) -> tuple[int, int]:
        events = await asyncio.to_thread(self.bot.calendar_sync.list_upcoming_events, 180)
        created = 0
        updated = 0
        skipped_existing = 0
        skipped_missing_start = 0
        user_id = self.bot.db.upsert_user(update.effective_user.id, update.effective_user.username, self.bot.settings.default_timezone)

        for event in events:
            event_id = str(event.get("id") or "").strip()
            if not event_id:
                continue
            if self.bot.db.is_calendar_event_tombstoned(event_id, provider="google", ttl_days=30):
                continue
            due_at_utc = self.calendar_event_to_due_utc(event)
            if not due_at_utc:
                skipped_missing_start += 1
                continue

            title = str(event.get("summary") or "Google Calendar event").strip()
            raw_notes = str(event.get("description") or "").strip()
            link = str(event.get("htmlLink") or "").strip() or self.extract_first_url(raw_notes)
            notes = self.clean_calendar_import_notes(raw_notes)

            mapped_reminder_id = self.bot.db.get_reminder_id_by_calendar_event_id(event_id, provider="google")
            if mapped_reminder_id:
                row = self.bot.db.get_reminder_by_id(mapped_reminder_id)
                if row is None:
                    continue
                reminder = dict(row)
                if reminder.get("status") != "open":
                    continue

                if not allow_update_existing:
                    skipped_existing += 1
                    continue

                changed = (
                    str(reminder.get("title") or "") != title
                    or str(reminder.get("notes") or "") != notes
                    or str(reminder.get("link") or "") != link
                    or str(reminder.get("due_at_utc") or "") != due_at_utc
                )
                if changed:
                    self.bot.db.update_reminder_fields(
                        reminder_id=mapped_reminder_id,
                        title=title,
                        topic=str(reminder.get("topic") or ""),
                        notes=notes,
                        link=link,
                        priority=str(reminder.get("priority") or "mid"),
                        due_at_utc=due_at_utc,
                        recurrence_rule=reminder.get("recurrence_rule"),
                    )
                    updated += 1
                continue

            reminder_id = self.bot.db.create_reminder(
                user_id=user_id,
                source_message_id=None,
                source_kind="google_calendar_import",
                title=title,
                topic="",
                notes=notes,
                link=link,
                priority="mid",
                due_at_utc=due_at_utc,
                timezone_name=self.bot.settings.default_timezone,
                chat_id_to_notify=update.effective_chat.id,
                recurrence_rule=None,
            )
            self.bot.db.upsert_calendar_event_id(reminder_id, event_id, provider="google")
            created += 1

        LOGGER.info(
            "Calendar pull processed events=%s created=%s updated=%s skipped_existing=%s skipped_no_start=%s",
            len(events),
            created,
            updated,
            skipped_existing,
            skipped_missing_start,
        )
        return created, updated

    def calendar_event_to_due_utc(self, event: dict) -> str:
        start = event.get("start") or {}
        if not isinstance(start, dict):
            return ""
        date_time_text = str(start.get("dateTime") or "").strip()
        if date_time_text:
            normalized = date_time_text.replace("Z", "+00:00")
            try:
                dt = datetime.fromisoformat(normalized)
            except ValueError:
                return ""
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc).isoformat()

        date_only_text = str(start.get("date") or "").strip()
        if not date_only_text:
            return ""
        try:
            date_only = datetime.strptime(date_only_text, "%Y-%m-%d")
        except ValueError:
            return ""
        try:
            tz = ZoneInfo(self.bot.settings.default_timezone)
        except Exception:
            tz = timezone.utc
        local_dt = date_only.replace(tzinfo=tz, hour=0, minute=0, second=0, microsecond=0)
        return local_dt.astimezone(timezone.utc).isoformat()

    def extract_first_url(self, text: str) -> str:
        match = re.search(r"https?://\S+", text or "")
        if not match:
            return ""
        return match.group(0).rstrip(").,]")

    def clean_calendar_import_notes(self, notes: str) -> str:
        lines = [line.rstrip() for line in (notes or "").splitlines()]
        cleaned: list[str] = []
        for line in lines:
            stripped = line.strip()
            lowered = stripped.lower()
            if not stripped:
                if cleaned and cleaned[-1] != "":
                    cleaned.append("")
                continue
            if lowered.startswith("reminder id:"):
                continue
            if lowered.startswith("priority:"):
                continue
            if lowered.startswith("link:"):
                continue
            if lowered.startswith("topic:"):
                continue
            cleaned.append(stripped)

        while cleaned and cleaned[-1] == "":
            cleaned.pop()
        return "\n".join(cleaned).strip()

    async def sync_calendar_upsert(self, reminder_id: int) -> None:
        if not self.bot.calendar_sync.is_enabled():
            return
        await asyncio.to_thread(self.bot.calendar_sync.upsert_for_reminder_id, reminder_id)

    async def sync_calendar_delete(self, reminder_id: int) -> None:
        if not self.bot.calendar_sync.is_enabled():
            return
        await asyncio.to_thread(self.bot.calendar_sync.delete_for_reminder_id, reminder_id)
