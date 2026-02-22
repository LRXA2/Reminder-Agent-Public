from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING

from src.app.messages import msg

if TYPE_CHECKING:
    from src.app.bot_orchestrator import ReminderBot


class ReminderLogicHandler:
    LONG_SUMMARY_NOTES_THRESHOLD = 250

    def __init__(self, bot: "ReminderBot") -> None:
        self.bot = bot

    def is_notes_list_candidate(self, row: dict) -> bool:
        notes = str(row.get("notes") or "").strip()
        if not notes:
            return False

        source_kind = str(row.get("source_kind") or "").strip().lower()
        if source_kind == "group_summary":
            return len(notes) >= self.LONG_SUMMARY_NOTES_THRESHOLD

        created_at = str(row.get("created_at_utc") or "").strip()
        updated_at = str(row.get("updated_at_utc") or "").strip()
        if created_at and updated_at and created_at != updated_at:
            return True

        return False

    def split_topics(self, topic_text: str) -> list[str]:
        seen: set[str] = set()
        result: list[str] = []
        for part in topic_text.split(","):
            value = part.strip()
            if not value:
                continue
            key = value.lower()
            if key in seen:
                continue
            seen.add(key)
            result.append(value)
        return result

    def format_missing_topics_message(self, chat_id: int, missing_topics: list[str]) -> str:
        base = msg("error_topics_missing_create", topics=", ".join(missing_topics))
        suggestions: list[str] = []
        for missing in missing_topics:
            suggestions.extend(self.bot.db.suggest_topics_for_chat(chat_id, missing, limit=3))
        deduped: list[str] = []
        seen: set[str] = set()
        for suggestion in suggestions:
            key = suggestion.lower()
            if key in seen:
                continue
            seen.add(key)
            deduped.append(suggestion)
        if not deduped:
            return base
        return base + "\n" + msg("status_topics_suggestions", topics=", ".join(deduped[:5]))

    def looks_like_inline_add_payload(self, raw: str) -> bool:
        text = (raw or "").lower()
        markers = (
            "at:",
            "p:",
            "priority:",
            "topic:",
            "t:",
            "every:",
            "link:",
            "notes:",
        )
        if any(marker in text for marker in markers):
            return True
        if re.search(r"(?:^|\s)#\w+", text):
            return True
        if re.search(r"(?:^|\s)!\s*(immediate|high|mid|low|i|h|m|l)\b", text):
            return True
        if re.search(r"(?:^|\s)@(daily|weekly|monthly)\b", text):
            return True
        return False

    def compute_next_due(self, due_at_utc: str, recurrence: str) -> str | None:
        try:
            current = datetime.fromisoformat(due_at_utc)
        except ValueError:
            return None
        if current.tzinfo is None:
            current = current.replace(tzinfo=timezone.utc)

        if recurrence == "daily":
            nxt = current + timedelta(days=1)
        elif recurrence == "weekly":
            nxt = current + timedelta(days=7)
        elif recurrence == "monthly":
            nxt = current + timedelta(days=30)
        else:
            return None
        return nxt.astimezone(timezone.utc).isoformat()
