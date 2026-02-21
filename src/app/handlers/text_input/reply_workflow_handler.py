from __future__ import annotations

import re

from src.app.handlers.intent_parsing import extract_due_and_priority
from src.app.handlers.reminder_formatting import format_reminder_brief
from src.app.messages import msg


class ReplyWorkflowHandler:
    def __init__(self, parent) -> None:
        self.parent = parent

    async def handle_reply_reminder(self, update, text: str) -> bool:
        if not update.message or not update.effective_user:
            return False
        replied = update.message.reply_to_message
        if not replied:
            return False

        details = extract_due_and_priority(text, self.parent.settings.default_timezone)
        missing_fields = []
        if not details.get("priority"):
            missing_fields.append("priority (immediate/high/mid/low)")
        if not details.get("due_at_utc"):
            missing_fields.append("due date/time")
        if missing_fields:
            await update.message.reply_text(
                "To add this as a reminder, include "
                + " and ".join(missing_fields)
                + ". Example: add as reminder high tomorrow 9am"
            )
            return True

        source_text = self.parent.summary_handler.message_text_with_links(replied)
        if not source_text:
            await update.message.reply_text(msg("error_text_only_reply"))
            return True

        title = self.title_from_reply_text(source_text)
        user_id = self.parent.db.upsert_user(
            update.effective_user.id,
            update.effective_user.username,
            self.parent.settings.default_timezone,
        )
        reminder_id = self.parent.db.create_reminder(
            user_id=user_id,
            source_message_id=None,
            source_kind="reply_message",
            title=title,
            topic="",
            notes=source_text[:1500],
            link=self.extract_first_url(source_text),
            priority=details["priority"],
            due_at_utc=details["due_at_utc"],
            timezone_name=self.parent.settings.default_timezone,
            chat_id_to_notify=update.effective_chat.id,
            recurrence_rule=None,
        )
        await update.message.reply_text(
            format_reminder_brief(reminder_id, title, details["due_at_utc"], self.parent.settings.default_timezone)
        )
        if self.parent.on_reminder_created:
            await self.parent.on_reminder_created(reminder_id)
        return True

    async def handle_reply_edit(self, update, text: str) -> bool:
        if not update.message:
            return False
        replied = update.message.reply_to_message
        if not replied:
            return False

        reminder_id = self.extract_reminder_id_from_text((replied.text or replied.caption or "").strip())
        if reminder_id is None:
            return False

        existing = self.parent.db.get_reminder_by_id_for_chat(reminder_id, update.effective_chat.id)
        if existing is None:
            await update.message.reply_text(msg("error_not_found", id=reminder_id))
            return True

        current = dict(existing)
        details = extract_due_and_priority(text, self.parent.settings.default_timezone)

        new_priority = details["priority"] if details.get("priority") else str(current.get("priority") or "mid")
        new_due = details["due_at_utc"] if details.get("due_at_utc") else str(current.get("due_at_utc") or "")
        new_title = self.extract_field_value(text, "title") or str(current.get("title") or "")

        notes_candidate = self.extract_field_value(text, "notes")
        if notes_candidate is None:
            if "clear notes" in text.lower():
                new_notes = ""
            else:
                new_notes = str(current.get("notes") or "")
        else:
            new_notes = notes_candidate

        link_candidate = self.extract_field_value(text, "link")
        if link_candidate is None:
            if "clear link" in text.lower():
                new_link = ""
            else:
                new_link = str(current.get("link") or "")
        else:
            new_link = link_candidate

        topic_candidate = self.extract_field_value(text, "topic") or self.extract_field_value(text, "t")
        if topic_candidate is None:
            if "clear topic" in text.lower():
                new_topic = ""
            else:
                new_topic = str(current.get("topic") or "")
        else:
            new_topic = topic_candidate

        recurrence = str(current.get("recurrence_rule") or "")
        every_match = re.search(r"every\s*:\s*(daily|weekly|monthly|none)\b", text, re.IGNORECASE)
        if every_match:
            parsed_recurrence = every_match.group(1).lower()
            recurrence = "" if parsed_recurrence == "none" else parsed_recurrence

        changed = (
            new_title != str(current.get("title") or "")
            or new_notes != str(current.get("notes") or "")
            or new_link != str(current.get("link") or "")
            or new_topic != str(current.get("topic") or "")
            or new_priority != str(current.get("priority") or "mid")
            or new_due != str(current.get("due_at_utc") or "")
            or recurrence != str(current.get("recurrence_rule") or "")
        )
        if not changed:
            await update.message.reply_text(
                "I found the reminder ID, but no editable fields were detected. "
                "Try: set to tomorrow 8am high, or title:<text>, topic:<text>, notes:<text>, link:<url>, every:weekly"
            )
            return True

        if not new_title.strip() or not new_due:
            await update.message.reply_text(msg("error_edit_must_keep"))
            return True

        ok = self.parent.db.update_reminder_fields_for_chat(
            reminder_id=reminder_id,
            chat_id_to_notify=update.effective_chat.id,
            title=new_title.strip(),
            topic=new_topic,
            notes=new_notes,
            link=new_link,
            priority=new_priority,
            due_at_utc=new_due,
            recurrence_rule=recurrence,
        )
        if not ok:
            await update.message.reply_text(msg("error_update_failed", id=reminder_id))
            return True

        if new_topic.strip():
            missing_topics = self.parent.db.has_missing_topics_for_chat(update.effective_chat.id, self.parent.common.split_topics(new_topic))
            if missing_topics:
                await update.message.reply_text(self.parent.common.format_missing_topics_message(update.effective_chat.id, missing_topics))
                return True
        self.parent.db.set_reminder_topics_for_chat(reminder_id, update.effective_chat.id, self.parent.common.split_topics(new_topic))

        await update.message.reply_text(
            format_reminder_brief(reminder_id, new_title, new_due, self.parent.settings.default_timezone)
        )
        if self.parent.on_reminder_updated:
            await self.parent.on_reminder_updated(reminder_id)
        return True

    def extract_reminder_id_from_text(self, text: str) -> int | None:
        match = re.search(r"(?:^|\n)ID\s*:\s*(\d+)\b", text, re.IGNORECASE)
        if not match:
            return None
        try:
            return int(match.group(1))
        except ValueError:
            return None

    def extract_field_value(self, text: str, field_name: str) -> str | None:
        pattern = rf"{field_name}\s*:\s*(.+?)(?=\s+(?:title|topic|t|notes|link|p|priority|at|every)\s*:|$)"
        match = re.search(pattern, text, re.IGNORECASE)
        if not match:
            return None
        return match.group(1).strip()

    def extract_first_url(self, text: str) -> str:
        match = re.search(r"https?://\S+", text)
        if not match:
            return ""
        return match.group(0).rstrip(").,]")

    def title_from_reply_text(self, source_text: str) -> str:
        skip_patterns = (
            "summary:",
            "possible follow-up actions",
            "schedule:",
            "location:",
            "attire:",
        )
        for raw_line in source_text.splitlines():
            line = raw_line.strip().strip("-*# ")
            if not line:
                continue
            lowered = line.lower().strip("*")
            if any(lowered.startswith(pattern) for pattern in skip_patterns):
                continue
            if lowered.startswith("course:"):
                course_name = line.split(":", 1)[1].strip() if ":" in line else ""
                if course_name:
                    return f"Review {course_name} schedule"
            if len(line) > 80:
                line = line[:80].rstrip() + "..."
            return line
        return "Review replied message"
