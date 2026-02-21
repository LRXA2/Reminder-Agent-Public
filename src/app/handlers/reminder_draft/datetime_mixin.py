from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from src.app.handlers.datetime_parser import parse_datetime_text


LOGGER = logging.getLogger(__name__)


class DraftDatetimeMixin:
    def _parse_due_to_utc(self, due_text: str, due_mode: str = "datetime") -> str:
        if not due_text:
            return ""
        parsed = parse_datetime_text(due_text, self.settings.default_timezone)
        if bool(getattr(self.settings, "datetime_parse_debug", False)):
            LOGGER.info(
                "draft_parse_due strategy=%s confidence=%s matched=%r input=%r",
                parsed.strategy,
                parsed.confidence,
                parsed.matched_text,
                due_text,
            )
        due_dt = parsed.dt
        if due_dt is None:
            return ""
        if due_mode in {"none", "unclear"} and not due_text.strip():
            return ""
        if due_mode == "all_day":
            due_dt = due_dt.replace(hour=0, minute=0, second=0, microsecond=0)
        elif due_mode in {"none", "unclear"}:
            due_dt = due_dt.replace(hour=0, minute=0, second=0, microsecond=0)
        try:
            from zoneinfo import ZoneInfo

            tz = ZoneInfo(self.settings.default_timezone)
        except Exception:
            tz = timezone.utc
        now_local = datetime.now(timezone.utc).astimezone(tz)
        if due_dt.astimezone(tz) < now_local - timedelta(days=1):
            recovered = self._infer_due_from_text(due_text)
            if recovered:
                return recovered
        return due_dt.astimezone(timezone.utc).isoformat()

    def _infer_due_from_text(self, text: str) -> str:
        if not text.strip():
            return ""
        parsed = parse_datetime_text(text, self.settings.default_timezone)
        if parsed.dt is None:
            return ""
        return parsed.dt.astimezone(timezone.utc).isoformat()
