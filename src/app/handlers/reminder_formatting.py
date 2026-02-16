from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo


def format_reminder_brief(reminder_id: int, title: str, due_at_utc: str, timezone_name: str) -> str:
    return "\n".join(
        [
            f"ID: {reminder_id}",
            f"Title: {title.strip()}",
            f"Date: {format_due_display(due_at_utc, timezone_name)}",
        ]
    )


def format_reminder_detail(row: dict, timezone_name: str) -> str:
    notes = (row.get("notes") or "").strip()
    lines = [
        f"ID: {row.get('id')}",
        f"Title: {row.get('title') or ''}",
        f"Date: {format_due_display(str(row.get('due_at_utc') or ''), timezone_name)}",
        f"Priority: {row.get('priority') or ''}",
        f"Status: {row.get('status') or ''}",
        f"Source: {row.get('source_kind') or ''}",
    ]
    if notes:
        lines.append("Details:")
        lines.append(notes)
    return "\n".join(lines)


def format_due_display(due_at_utc: str, timezone_name: str) -> str:
    if not due_at_utc:
        return "(none)"

    try:
        dt = datetime.fromisoformat(due_at_utc)
    except ValueError:
        return due_at_utc

    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)

    try:
        target_tz = ZoneInfo(timezone_name)
    except Exception:
        target_tz = timezone.utc

    local = dt.astimezone(target_tz)
    if local.hour == 0 and local.minute == 0:
        return local.strftime("%d/%m/%y")
    return local.strftime("%d/%m/%y %H:%M")
