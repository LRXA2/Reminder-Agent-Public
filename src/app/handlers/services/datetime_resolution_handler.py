from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

import dateparser

from src.app.handlers.datetime_parser import parse_datetime_text
from src.app.prompts import datetime_fallback_prompt

if TYPE_CHECKING:
    from src.app.bot_orchestrator import ReminderBot


LOGGER = logging.getLogger(__name__)


class DateTimeResolutionHandler:
    def __init__(self, bot: "ReminderBot") -> None:
        self.bot = bot

    def parse_natural_datetime(self, dt_text: str) -> tuple[datetime | None, str]:
        parsed = parse_datetime_text(dt_text, self.bot.settings.default_timezone)
        if parsed.dt is not None:
            if self.bot.settings.datetime_parse_debug:
                LOGGER.info(
                    "parse_natural strategy=%s confidence=%s matched=%r input=%r",
                    parsed.strategy,
                    parsed.confidence,
                    parsed.matched_text,
                    dt_text,
                )
            return parsed.dt, parsed.confidence

        try:
            tz = ZoneInfo(self.bot.settings.default_timezone)
        except Exception:
            tz = timezone.utc
        now_local = datetime.now(tz)

        parsed_llm = self.parse_datetime_with_llm(dt_text, now_local)
        if parsed_llm is not None:
            return parsed_llm, "medium"
        return None, "low"

    def normalize_all_day_datetime(self, parsed_dt: datetime, raw_text: str) -> datetime:
        if self.has_explicit_time(raw_text):
            return parsed_dt
        try:
            tz = ZoneInfo(self.bot.settings.default_timezone)
        except Exception:
            tz = timezone.utc
        local = parsed_dt.astimezone(tz) if parsed_dt.tzinfo else parsed_dt.replace(tzinfo=tz)
        return local.replace(hour=0, minute=0, second=0, microsecond=0)

    def has_explicit_time(self, raw_text: str) -> bool:
        text = (raw_text or "").strip().lower()
        if not text:
            return False
        if re.search(r"\b([01]?\d|2[0-3]):[0-5]\d\b", text):
            return True
        if re.search(r"\b\d{1,2}\s*(am|pm)\b", text):
            return True
        if re.search(r"\b\d{1,2}:[0-5]\d\s*(am|pm)\b", text):
            return True
        return any(token in text for token in ("noon", "midnight", "morning", "afternoon", "evening", "tonight"))

    def parse_datetime_with_llm(self, raw_text: str, now_local: datetime) -> datetime | None:
        prompt = datetime_fallback_prompt(
            user_text=raw_text,
            timezone_name=self.bot.settings.default_timezone,
            now_iso=now_local.isoformat(),
        )
        raw = self.bot.ollama.generate_text(prompt)
        parsed = self.parse_json_object(raw)
        if not parsed:
            return None

        confidence = str(parsed.get("confidence") or "").strip().lower()
        if confidence not in {"high", "medium"}:
            return None

        due_mode = str(parsed.get("due_mode") or "datetime").strip().lower()
        if due_mode not in {"datetime", "all_day", "none", "unclear"}:
            due_mode = "datetime"
        if due_mode in {"none", "unclear"}:
            return None

        due_text = str(parsed.get("due_text") or "").strip()
        if not due_text:
            return None

        due_dt = dateparser.parse(
            due_text,
            settings={
                "TIMEZONE": self.bot.settings.default_timezone,
                "RETURN_AS_TIMEZONE_AWARE": True,
                "PREFER_DATES_FROM": "future",
                "RELATIVE_BASE": now_local,
            },
        )
        if due_dt is not None and due_mode == "all_day":
            due_dt = self.normalize_all_day_datetime(due_dt, due_text)
        return due_dt

    def parse_json_object(self, text: str) -> dict[str, str] | None:
        try:
            loaded = json.loads(text)
            if isinstance(loaded, dict):
                return {str(k): str(v) for k, v in loaded.items()}
        except Exception:
            pass

        match = re.search(r"\{[\s\S]*\}", text)
        if not match:
            return None
        try:
            loaded = json.loads(match.group(0))
        except Exception:
            return None
        if not isinstance(loaded, dict):
            return None
        return {str(k): str(v) for k, v in loaded.items()}
