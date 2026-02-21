from __future__ import annotations

import re
from datetime import timezone

import dateparser
from dateparser.search import search_dates


def has_summary_intent(lowered_text: str) -> bool:
    patterns = [
        "summarize for me",
        "help me summarize",
        "summarize it for me",
        "can you summarize",
        "summarize this",
        "summarize this document",
        "summarise this document",
        "summarize this pdf",
        "summarise this pdf",
    ]
    return any(p in lowered_text for p in patterns)


def has_image_summary_intent(lowered_text: str) -> bool:
    patterns = [
        "summarize this image",
        "summarise this image",
        "summarize image",
        "summarise image",
        "describe this image",
        "what is in this image",
        "what's in this image",
        "analyze this image",
        "analyse this image",
        "summarize this",
        "summarize for me",
    ]
    return any(p in lowered_text for p in patterns)


def has_hackathon_query_intent(lowered_text: str) -> bool:
    if "hackathon" not in lowered_text and "hackathons" not in lowered_text:
        return False
    query_markers = ["what", "which", "available", "list", "show", "between", "from"]
    return any(marker in lowered_text for marker in query_markers)


def has_reminder_intent(lowered_text: str) -> bool:
    phrases = (
        "remind me",
        "todo",
        "create reminder",
        "create a reminder",
        "set reminder",
        "set as reminder",
        "add as reminder",
    )
    return any(phrase in lowered_text for phrase in phrases)


def has_edit_intent(lowered_text: str) -> bool:
    phrases = (
        "set to",
        "change to",
        "update to",
        "reschedule",
        "move to",
        "edit reminder",
        "update reminder",
    )
    return any(phrase in lowered_text for phrase in phrases)


def extract_summary_content(text: str) -> str:
    markers = [
        "summarize for me",
        "help me summarize",
        "summarize it for me",
        "can you summarize",
    ]
    lowered = text.lower()
    start = -1
    marker_used = ""
    for marker in markers:
        idx = lowered.find(marker)
        if idx >= 0:
            start = idx
            marker_used = marker
            break
    if start < 0:
        return ""
    return text[start + len(marker_used) :].strip()


def extract_due_and_priority(text: str, timezone_name: str) -> dict[str, str]:
    priority_match = re.search(r"(?:p|priority)?\s*:?\s*(immediate|high|mid|low)\b", text, re.IGNORECASE)
    priority = priority_match.group(1).lower() if priority_match else ""

    due_dt = None
    found = search_dates(
        text,
        settings={
            "TIMEZONE": timezone_name,
            "RETURN_AS_TIMEZONE_AWARE": True,
            "PREFER_DATES_FROM": "future",
        },
    )
    if found:
        due_dt = found[-1][1]
    elif any(token in text.lower() for token in ("today", "tomorrow", "tonight", "next ")):
        due_dt = dateparser.parse(
            text,
            settings={
                "TIMEZONE": timezone_name,
                "RETURN_AS_TIMEZONE_AWARE": True,
                "PREFER_DATES_FROM": "future",
            },
        )

    if due_dt is not None and not _has_explicit_time(text):
        due_dt = due_dt.replace(hour=0, minute=0, second=0, microsecond=0)

    due_utc = due_dt.astimezone(timezone.utc).isoformat() if due_dt else ""
    return {
        "priority": priority,
        "due_at_utc": due_utc,
    }


def _has_explicit_time(raw_text: str) -> bool:
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
