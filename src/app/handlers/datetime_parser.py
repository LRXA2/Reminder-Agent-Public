from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import dateparser
from dateparser.search import search_dates


@dataclass
class DateParseResult:
    dt: datetime | None
    confidence: str
    matched_text: str
    strategy: str


def parse_datetime_text(raw_text: str, timezone_name: str, now_local: datetime | None = None) -> DateParseResult:
    text = _normalize_common_typos((raw_text or "").strip())
    if not text:
        return DateParseResult(dt=None, confidence="low", matched_text="", strategy="empty")

    tz = _safe_tz(timezone_name)
    now = now_local or datetime.now(tz)

    explicit = _extract_explicit_date(text, timezone_name, now)
    if explicit is not None:
        phrase, dt_value, phrase_has_time = explicit
        dt_value = _apply_time_if_missing(dt_value, text, phrase_has_time)
        return DateParseResult(dt=dt_value, confidence="high", matched_text=phrase, strategy="explicit")

    relative = _parse_relative_day_phrase(text, now)
    if relative is not None:
        phrase, dt_value = relative
        confidence = "high" if _has_explicit_time(text) else "medium"
        return DateParseResult(dt=dt_value, confidence=confidence, matched_text=phrase, strategy="relative")

    parsed = dateparser.parse(
        text,
        settings={
            "TIMEZONE": timezone_name,
            "RETURN_AS_TIMEZONE_AWARE": True,
            "PREFER_DATES_FROM": "future",
            "RELATIVE_BASE": now,
        },
    )
    if parsed is not None:
        parsed = _apply_time_if_missing(parsed, text, _has_explicit_time(text))
        return DateParseResult(dt=parsed, confidence=_estimate_confidence(text), matched_text=text, strategy="direct")

    found = search_dates(
        text,
        settings={
            "TIMEZONE": timezone_name,
            "RETURN_AS_TIMEZONE_AWARE": True,
            "PREFER_DATES_FROM": "future",
            "RELATIVE_BASE": now,
        },
    )
    if not found:
        return DateParseResult(dt=None, confidence="low", matched_text="", strategy="none")

    picked = _pick_best_search_date(found)
    if picked is None:
        return DateParseResult(dt=None, confidence="low", matched_text="", strategy="none")
    phrase, dt_value = picked
    dt_value = _apply_time_if_missing(dt_value, text, _has_explicit_time(phrase))
    return DateParseResult(dt=dt_value, confidence=_estimate_confidence(phrase), matched_text=phrase, strategy="search")


def _normalize_common_typos(text: str) -> str:
    normalized = text
    typo_map = {
        r"\btomrrow\b": "tomorrow",
        r"\btmrrow\b": "tomorrow",
        r"\btommorow\b": "tomorrow",
        r"\btommorrow\b": "tomorrow",
        r"\bthur\b": "thu",
        r"\bthurday\b": "thursday",
        r"\btmr\b": "tomorrow",
    }
    for pattern, replacement in typo_map.items():
        normalized = re.sub(pattern, replacement, normalized, flags=re.IGNORECASE)
    return normalized


def _safe_tz(timezone_name: str):
    try:
        return ZoneInfo(timezone_name)
    except Exception:
        return timezone.utc


def _has_explicit_time(text: str) -> bool:
    lowered = (text or "").strip().lower()
    return bool(
        re.search(r"\b([01]?\d|2[0-3]):[0-5]\d\b", lowered)
        or re.search(r"\b\d{1,2}\s*(am|pm)\b", lowered)
        or re.search(r"\b\d{1,2}:[0-5]\d\s*(am|pm)\b", lowered)
        or any(token in lowered for token in ("noon", "midnight", "morning", "afternoon", "evening", "tonight"))
    )


def _extract_time_of_day(text: str) -> tuple[int, int] | None:
    lowered = (text or "").lower()
    ampm = re.search(r"\b(\d{1,2})(?::([0-5]\d))?\s*(am|pm)\b", lowered)
    if ampm:
        hour = int(ampm.group(1)) % 12
        minute = int(ampm.group(2) or "0")
        if ampm.group(3) == "pm":
            hour += 12
        return hour, minute
    h24 = re.search(r"\b([01]?\d|2[0-3]):([0-5]\d)\b", lowered)
    if h24:
        return int(h24.group(1)), int(h24.group(2))
    return None


def _apply_time_if_missing(dt_value: datetime, source_text: str, phrase_has_time: bool) -> datetime:
    extracted_time = _extract_time_of_day(source_text)
    if extracted_time is not None:
        hour, minute = extracted_time
        return dt_value.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if phrase_has_time:
        return dt_value
    return dt_value.replace(hour=0, minute=0, second=0, microsecond=0)


def _estimate_confidence(text: str) -> str:
    lowered = (text or "").strip().lower()
    explicit_date = bool(
        re.search(r"\b\d{1,2}[/-]\d{1,2}([/-]\d{2,4})?\b", lowered)
        or re.search(r"\b\d{4}-\d{1,2}-\d{1,2}\b", lowered)
        or re.search(
            r"\b(jan|january|feb|february|mar|march|apr|april|may|jun|june|jul|july|aug|august|sep|sept|september|oct|october|nov|november|dec|december)\b",
            lowered,
        )
    )
    explicit_time = _has_explicit_time(lowered)
    relative = any(token in lowered for token in ("today", "tomorrow", "next", "this", "mon", "tue", "wed", "thu", "fri", "sat", "sun"))
    if explicit_date or (relative and explicit_time):
        return "high"
    if relative:
        return "medium"
    return "low"


def _extract_explicit_date(text: str, timezone_name: str, now_local: datetime) -> tuple[str, datetime, bool] | None:
    month_names = "jan|january|feb|february|mar|march|apr|april|may|jun|june|jul|july|aug|august|sep|sept|september|oct|october|nov|november|dec|december"
    patterns = [
        rf"\b(?:{month_names})\s+\d{{1,2}}(?:,\s*\d{{4}})?\b",
        rf"\b\d{{1,2}}\s+(?:{month_names})(?:\s+\d{{4}})?\b",
        r"\b\d{1,2}[/-]\d{1,2}(?:[/-]\d{2,4})?\b",
        r"\b\d{4}-\d{1,2}-\d{1,2}\b",
    ]
    candidates: list[tuple[str, datetime, bool]] = []
    for pattern in patterns:
        for match in re.finditer(pattern, text, re.IGNORECASE):
            raw = match.group(0).strip()
            parsed = dateparser.parse(
                raw,
                settings={
                    "TIMEZONE": timezone_name,
                    "RETURN_AS_TIMEZONE_AWARE": True,
                    "PREFER_DATES_FROM": "future",
                    "RELATIVE_BASE": now_local,
                },
            )
            if parsed is None:
                continue
            candidates.append((raw, parsed, _has_explicit_time(raw)))
    if not candidates:
        return None
    future = [item for item in candidates if item[1] >= now_local - timedelta(days=1)]
    if not future:
        return None
    future.sort(key=lambda x: x[1])
    return future[0]


def _parse_relative_day_phrase(text: str, now_local: datetime) -> tuple[str, datetime] | None:
    cleaned = re.sub(r"\s+", " ", text.strip().lower())
    if not cleaned:
        return None

    simple_match = re.match(r"^(today|later today|tomorrow|tmr|tmrw|tonight)(?:\s+(?:at\s+)?)?(.*)$", cleaned)
    if simple_match:
        keyword = simple_match.group(1)
        rest = (simple_match.group(2) or "").strip()
        if keyword in {"today", "later today"}:
            base = now_local.replace(hour=18, minute=0, second=0, microsecond=0)
        elif keyword in {"tomorrow", "tmr", "tmrw"}:
            base = (now_local + timedelta(days=1)).replace(hour=9, minute=0, second=0, microsecond=0)
        else:  # tonight
            base = now_local.replace(hour=20, minute=0, second=0, microsecond=0)

        if rest:
            parsed_time = dateparser.parse(
                rest,
                settings={
                    "TIMEZONE": str(now_local.tzinfo or "UTC"),
                    "RETURN_AS_TIMEZONE_AWARE": True,
                    "RELATIVE_BASE": base,
                    "PREFER_DATES_FROM": "future",
                },
            )
            if parsed_time is not None:
                base = base.replace(hour=parsed_time.hour, minute=parsed_time.minute, second=0, microsecond=0)
                return keyword, base
        return keyword, _apply_time_if_missing(base, text, _has_explicit_time(text))

    match = re.search(
        r"\b(?:(next|this|coming|upcoming)\s+)?(mon(?:day)?|tue(?:s|sday)?|wed(?:nesday)?|thu(?:r|rs|rsday)?|fri(?:day)?|sat(?:urday)?|sun(?:day)?)\b",
        cleaned,
    )
    if not match:
        return None

    qualifier = (match.group(1) or "").lower()
    weekday_name = (match.group(2) or "").lower()
    weekday_map = {
        "mon": 0,
        "monday": 0,
        "tue": 1,
        "tues": 1,
        "tuesday": 1,
        "wed": 2,
        "wednesday": 2,
        "thu": 3,
        "thur": 3,
        "thurs": 3,
        "thursday": 3,
        "fri": 4,
        "friday": 4,
        "sat": 5,
        "saturday": 5,
        "sun": 6,
        "sunday": 6,
    }
    target_weekday = weekday_map.get(weekday_name)
    if target_weekday is None:
        return None

    days_ahead = (target_weekday - now_local.weekday()) % 7
    if qualifier in {"next", "coming", "upcoming"}:
        days_ahead = 7 if days_ahead == 0 else days_ahead
    elif days_ahead == 0:
        days_ahead = 7

    base = (now_local + timedelta(days=days_ahead)).replace(hour=9, minute=0, second=0, microsecond=0)
    return match.group(0), _apply_time_if_missing(base, text, _has_explicit_time(text))


def _pick_best_search_date(found: list[tuple[str, datetime]]) -> tuple[str, datetime] | None:
    best: tuple[str, datetime] | None = None
    best_score = -1
    for phrase, dt_value in found:
        lowered = (phrase or "").strip().lower()
        if not lowered or re.fullmatch(r"\d+", lowered):
            continue
        score = 0
        if _has_explicit_time(lowered):
            score += 3
        if any(token in lowered for token in ("today", "tomorrow", "next", "this", "mon", "tue", "wed", "thu", "fri", "sat", "sun")):
            score += 3
        if re.search(r"\b\d{1,2}[/-]\d{1,2}([/-]\d{2,4})?\b", lowered):
            score += 2
        if score > best_score:
            best_score = score
            best = (phrase, dt_value)
    return best
