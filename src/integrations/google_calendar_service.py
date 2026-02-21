from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from src.core.config import Settings
from src.storage.database import Database


LOGGER = logging.getLogger(__name__)


class GoogleCalendarSyncService:
    def __init__(self, settings: Settings, db: Database):
        self.settings = settings
        self.db = db
        self.enabled = bool(settings.gcal_sync_enabled)
        self.calendar_id = settings.gcal_calendar_id or "primary"
        self.credentials_file = settings.gcal_credentials_file
        self._service = None
        self._service_error = ""
        self._last_error = ""

    def is_enabled(self) -> bool:
        return self.enabled

    def get_last_error(self) -> str:
        return self._last_error

    def upsert_for_reminder_id(self, reminder_id: int) -> bool:
        if not self.enabled:
            self._last_error = "sync disabled"
            return False

        row = self.db.get_reminder_by_id(reminder_id)
        if row is None:
            self._last_error = f"reminder #{reminder_id} not found"
            return False
        reminder = dict(row)
        if reminder.get("status") != "open":
            return self.delete_for_reminder_id(reminder_id)
        if not str(reminder.get("due_at_utc") or "").strip():
            # No due date -> not a calendar event.
            self.db.delete_calendar_event_id(reminder_id, provider="google")
            self._last_error = f"reminder #{reminder_id} has no due date"
            return False

        service = self._get_service()
        if service is None:
            self._last_error = self._service_error or "calendar service unavailable"
            LOGGER.warning("Google Calendar sync skipped: %s", self._last_error)
            return False

        event_body = self._build_event_body(reminder)
        existing_event_id = self.db.get_calendar_event_id(reminder_id, provider="google")
        try:
            if existing_event_id:
                updated = (
                    service.events()
                    .update(calendarId=self.calendar_id, eventId=existing_event_id, body=event_body)
                    .execute()
                )
                event_id = str(updated.get("id") or existing_event_id)
            else:
                created = service.events().insert(calendarId=self.calendar_id, body=event_body).execute()
                event_id = str(created.get("id") or "")
                if not event_id:
                    return False

            self.db.upsert_calendar_event_id(reminder_id, event_id, provider="google")
            self._last_error = ""
            return True
        except Exception as exc:
            self._last_error = str(exc)
            LOGGER.warning("Google Calendar upsert failed for reminder %s: %s", reminder_id, exc)
            return False

    def delete_for_reminder_id(self, reminder_id: int) -> bool:
        if not self.enabled:
            return False
        event_id = self.db.get_calendar_event_id(reminder_id, provider="google")
        if not event_id:
            return False

        service = self._get_service()
        if service is None:
            LOGGER.warning("Google Calendar delete skipped: %s", self._service_error)
            return False

        try:
            service.events().delete(calendarId=self.calendar_id, eventId=event_id).execute()
        except Exception as exc:
            LOGGER.warning("Google Calendar delete failed for reminder %s: %s", reminder_id, exc)
            return False
        self.db.delete_calendar_event_id(reminder_id, provider="google")
        return True

    def list_upcoming_events(self, lookahead_days: int = 180) -> list[dict]:
        if not self.enabled:
            return []
        service = self._get_service()
        if service is None:
            LOGGER.warning("Google Calendar list skipped: %s", self._service_error)
            return []

        now = datetime.now(timezone.utc)
        time_min = now.isoformat()
        time_max = (now + timedelta(days=max(1, lookahead_days))).isoformat()
        try:
            response = (
                service.events()
                .list(
                    calendarId=self.calendar_id,
                    timeMin=time_min,
                    timeMax=time_max,
                    singleEvents=True,
                    orderBy="startTime",
                    maxResults=2500,
                )
                .execute()
            )
            items = response.get("items") or []
            cleaned: list[dict] = []
            for item in items:
                if not isinstance(item, dict):
                    continue
                if str(item.get("status") or "").lower() == "cancelled":
                    continue
                event_id = str(item.get("id") or "").strip()
                if not event_id:
                    continue
                cleaned.append(item)
            return cleaned
        except Exception as exc:
            LOGGER.warning("Google Calendar list events failed: %s", exc)
            return []

    def _get_service(self):
        if self._service is not None:
            return self._service

        if not self.credentials_file:
            self._service_error = "GCAL_CREDENTIALS_FILE is not set"
            return None

        try:
            from google.oauth2.service_account import Credentials
            from googleapiclient.discovery import build
        except Exception as exc:
            self._service_error = f"google client libraries unavailable: {exc}"
            return None

        try:
            creds = Credentials.from_service_account_file(
                self.credentials_file,
                scopes=["https://www.googleapis.com/auth/calendar"],
            )
            self._service = build("calendar", "v3", credentials=creds, cache_discovery=False)
            return self._service
        except Exception as exc:
            self._service_error = f"credentials/service initialization failed: {exc}"
            return None

    def _build_event_body(self, reminder: dict) -> dict:
        due_at_utc = str(reminder.get("due_at_utc") or "")
        due_dt = datetime.fromisoformat(due_at_utc)
        if due_dt.tzinfo is None:
            due_dt = due_dt.replace(tzinfo=timezone.utc)
        try:
            local_tz = ZoneInfo(self.settings.default_timezone)
        except Exception:
            local_tz = timezone.utc
        due_local = due_dt.astimezone(local_tz)
        end_dt = due_dt + timedelta(minutes=30)

        notes = (reminder.get("notes") or "").strip()
        link = (reminder.get("link") or "").strip()
        topic = (reminder.get("topics_text") or reminder.get("topic") or "").strip()
        description_parts = [f"Reminder ID: {reminder.get('id')}", f"Priority: {reminder.get('priority')}"]
        if topic:
            description_parts.append(f"Topic: {topic}")
        if link:
            description_parts.append(f"Link: {link}")
        if notes:
            description_parts.append("")
            description_parts.append(notes)

        body = {
            "summary": str(reminder.get("title") or "Reminder"),
            "description": "\n".join(description_parts),
            "reminders": {"useDefault": True},
        }

        is_all_day = due_local.hour == 0 and due_local.minute == 0 and due_local.second == 0
        if is_all_day:
            start_date = due_local.date().isoformat()
            end_date = (due_local.date() + timedelta(days=1)).isoformat()
            body["start"] = {"date": start_date}
            body["end"] = {"date": end_date}
        else:
            body["start"] = {"dateTime": due_dt.astimezone(timezone.utc).isoformat(), "timeZone": "UTC"}
            body["end"] = {"dateTime": end_dt.astimezone(timezone.utc).isoformat(), "timeZone": "UTC"}
        return body
