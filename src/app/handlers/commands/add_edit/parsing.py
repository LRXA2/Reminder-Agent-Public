from __future__ import annotations

import logging
import re
from datetime import timezone
from typing import TYPE_CHECKING

from src.app.handlers.datetime_parser import parse_datetime_text
from src.app.messages import msg

if TYPE_CHECKING:
    from src.app.bot_orchestrator import ReminderBot


LOGGER = logging.getLogger(__name__)


class AddEditPayloadParser:
    def __init__(self, bot: "ReminderBot") -> None:
        self.bot = bot

    def parse_add_payload(self, payload: str) -> dict[str, str]:
        text = payload.strip()
        links = re.findall(r"https?://\S+", text)
        first_link = links[0].rstrip(").,]") if links else ""

        topic_parts: list[str] = []
        topic_match = re.search(r"(?:topic|t)\s*:\s*(.+?)(?=\s+(?:link|p|priority|at|every)\s*:|$)", text, re.IGNORECASE)
        if topic_match:
            topic_parts.extend(self.bot.reminder_logic_handler.split_topics(topic_match.group(1).strip()))
            text = (text[: topic_match.start()] + text[topic_match.end() :]).strip()

        hashtag_topics = re.findall(r"(?<!\w)#([A-Za-z0-9][A-Za-z0-9_-]{0,40})\b", text)
        if hashtag_topics:
            topic_parts.extend(hashtag_topics)
            text = re.sub(r"(?<!\w)#[A-Za-z0-9][A-Za-z0-9_-]{0,40}\b", " ", text)

        topic = ",".join(self.bot.reminder_logic_handler.split_topics(",".join(topic_parts)))

        priority_match = re.search(r"(?:p|priority)\s*:\s*(immediate|high|mid|low)\b", text, re.IGNORECASE)
        priority = priority_match.group(1).lower() if priority_match else "mid"
        if priority_match:
            text = text[: priority_match.start()] + text[priority_match.end() :]
        else:
            bang_priority_match = re.search(r"(?:^|\s)!\s*(immediate|high|mid|low|i|h|m|l)\b", text, re.IGNORECASE)
            if bang_priority_match:
                token = bang_priority_match.group(1).lower()
                priority = {
                    "i": "immediate",
                    "h": "high",
                    "m": "mid",
                    "l": "low",
                }.get(token, token)
                text = text[: bang_priority_match.start()] + text[bang_priority_match.end() :]

        recur_match = re.search(r"every\s*:\s*(daily|weekly|monthly)\b", text, re.IGNORECASE)
        recurrence = recur_match.group(1).lower() if recur_match else ""
        if recur_match:
            text = text[: recur_match.start()] + text[recur_match.end() :]
        else:
            recur_short_match = re.search(r"(?:^|\s)@(daily|weekly|monthly)\b", text, re.IGNORECASE)
            if recur_short_match:
                recurrence = recur_short_match.group(1).lower()
                text = text[: recur_short_match.start()] + text[recur_short_match.end() :]

        due_dt = None
        due_confidence = "low"
        no_due_requested = False
        at_match = re.search(r"at\s*:\s*(.+?)(?=\s+(?:topic|t|link|p|priority|every)\s*:|$)", text, re.IGNORECASE)
        if at_match:
            dt_text = at_match.group(1).strip()
            if self.is_no_due_text(dt_text):
                no_due_requested = True
            else:
                due_dt, due_confidence = self.bot.datetime_resolution_handler.parse_natural_datetime(dt_text)
            text = text[: at_match.start()].strip()
        else:
            no_due_match = re.search(r"\b(no\s+due(?:\s+date)?|no\s+deadline|someday|backlog)\b", text, re.IGNORECASE)
            if no_due_match:
                no_due_requested = True
                text = (text[: no_due_match.start()] + text[no_due_match.end() :]).strip()
            else:
                parsed_search = parse_datetime_text(text, self.bot.settings.default_timezone)
                if parsed_search.dt is not None:
                    if self.bot.settings.datetime_parse_debug:
                        LOGGER.info(
                            "parse_add datetime strategy=%s confidence=%s matched=%r input=%r",
                            parsed_search.strategy,
                            parsed_search.confidence,
                            parsed_search.matched_text,
                            text,
                        )
                    due_dt = parsed_search.dt
                    due_confidence = parsed_search.confidence
                    if parsed_search.matched_text:
                        text = text.replace(parsed_search.matched_text, " ").strip()

        cleaned = re.sub(r"\s+", " ", text).strip(" -")
        cleaned = re.sub(r"^(remind me to|remind me|todo)\s+", "", cleaned, flags=re.IGNORECASE).strip()

        if not cleaned:
            return {"error": msg("error_add_missing_title")}

        if due_dt is None and not no_due_requested:
            return {"error": msg("error_add_missing_due")}

        due_utc = due_dt.astimezone(timezone.utc).isoformat() if due_dt is not None else ""
        return {
            "title": cleaned,
            "topic": topic,
            "priority": priority,
            "due_at_utc": due_utc,
            "recurrence": recurrence,
            "link": first_link,
            "needs_confirmation": "true" if (due_dt is not None and due_confidence != "high") else "",
        }

    def parse_edit_payload(self, payload: str) -> dict[str, object]:
        text = payload.strip()

        title: str | None = None
        notes: str | None = None
        link: str | None = None
        topic_mode: str | None = None
        topic_values: list[str] = []
        priority: str | None = None
        due_at_utc: str | None = None
        recurrence: str | None = None

        title_match = re.search(r"title\s*:\s*(.+?)(?=\s+(?:topic|t|notes|link|p|priority|at|every)\s*:|$)", text, re.IGNORECASE)
        if title_match:
            title = title_match.group(1).strip()

        notes_match = re.search(r"notes\s*:\s*(.+?)(?=\s+(?:title|topic|t|link|p|priority|at|every)\s*:|$)", text, re.IGNORECASE)
        if notes_match:
            notes = notes_match.group(1).strip()

        link_match = re.search(r"link\s*:\s*(.+?)(?=\s+(?:title|topic|t|notes|p|priority|at|every)\s*:|$)", text, re.IGNORECASE)
        if link_match:
            link = link_match.group(1).strip()

        topic_match = re.search(r"(?:topic|t)\s*:\s*(.+?)(?=\s+(?:title|notes|link|p|priority|at|every)\s*:|$)", text, re.IGNORECASE)
        if topic_match:
            parsed_topic = topic_match.group(1).strip()
            parsed_lower = parsed_topic.lower()
            if parsed_lower in {"none", "clear", "null", "n/a", "na", "-"}:
                topic_mode = "clear"
            elif parsed_topic.startswith("+"):
                topic_mode = "add"
                topic_values = self.bot.reminder_logic_handler.split_topics(",".join(part.strip().lstrip("+") for part in parsed_topic.split(",")))
            elif parsed_topic.startswith("-"):
                topic_mode = "remove"
                topic_values = self.bot.reminder_logic_handler.split_topics(",".join(part.strip().lstrip("-") for part in parsed_topic.split(",")))
            else:
                topic_mode = "replace"
                topic_values = self.bot.reminder_logic_handler.split_topics(parsed_topic)

        priority_match = re.search(r"(?:p|priority)\s*:\s*(immediate|high|mid|low)\b", text, re.IGNORECASE)
        if priority_match:
            priority = priority_match.group(1).lower()

        recur_match = re.search(r"every\s*:\s*(daily|weekly|monthly|none)\b", text, re.IGNORECASE)
        if recur_match:
            recurrence = recur_match.group(1).lower()
            if recurrence == "none":
                recurrence = ""

        at_match = re.search(r"at\s*:\s*(.+?)(?=\s+(?:title|topic|t|notes|link|p|priority|every)\s*:|$)", text, re.IGNORECASE)
        if at_match:
            dt_text = at_match.group(1).strip()
            if self.is_no_due_text(dt_text):
                due_at_utc = ""
            else:
                due_dt, _due_confidence = self.bot.datetime_resolution_handler.parse_natural_datetime(dt_text)
                if due_dt is None:
                    return {"error": msg("error_edit_invalid_due")}
                due_at_utc = due_dt.astimezone(timezone.utc).isoformat()

        if not any(value is not None for value in (title, notes, link, priority, due_at_utc, recurrence)) and not topic_mode:
            title = text

        return {
            "title": title,
            "topic_mode": topic_mode,
            "topic_values": topic_values,
            "notes": notes,
            "link": link,
            "priority": priority,
            "due_at_utc": due_at_utc,
            "recurrence": recurrence,
            "error": None,
        }

    def is_no_due_text(self, text: str) -> bool:
        normalized = re.sub(r"\s+", " ", (text or "").strip().lower())
        return normalized in {
            "none",
            "no due",
            "no due date",
            "no deadline",
            "someday",
            "backlog",
            "na",
            "n/a",
        }
