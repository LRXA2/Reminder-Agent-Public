from __future__ import annotations

import re
from datetime import timezone
from typing import TYPE_CHECKING

from telegram import Update

from src.app.handlers.reminder_formatting import format_reminder_brief
from src.app.messages import msg

if TYPE_CHECKING:
    from src.app.bot_orchestrator import ReminderBot


class AddWizardWorkflow:
    def __init__(self, bot: "ReminderBot") -> None:
        self.bot = bot

    async def handle_pending_add_wizard(self, update: Update, text: str) -> bool:
        if not update.message or not update.effective_user:
            return False
        chat_id = update.effective_chat.id
        state = self.bot.pending_add_wizards.get(chat_id)
        if not state:
            return False

        raw = (text or "").strip()
        lowered = raw.lower()
        if lowered in {"cancel", "stop"}:
            self.bot.pending_add_wizards.pop(chat_id, None)
            await update.message.reply_text("Add flow cancelled.")
            return True

        step = state.get("step", "due")
        if step == "due":
            if lowered not in {"skip", "none", "no"}:
                due_dt, _conf = self.bot.datetime_resolution_handler.parse_natural_datetime(raw)
                if due_dt is None:
                    await update.message.reply_text("Could not parse date/time. Try `tomorrow 9am`, `next fri 3pm`, or `skip`.")
                    return True
                state["due_at_utc"] = due_dt.astimezone(timezone.utc).isoformat()
            else:
                state["due_at_utc"] = ""
            state["step"] = "priority"
            await update.message.reply_text("Step 2/5 - Priority? `immediate`, `high`, `mid`, `low` or `skip` (mid).")
            return True

        if step == "priority":
            if lowered not in {"skip", "none", "no"}:
                token = lowered.strip()
                token = {"i": "immediate", "h": "high", "m": "mid", "l": "low"}.get(token, token)
                if token not in {"immediate", "high", "mid", "low"}:
                    await update.message.reply_text("Invalid priority. Use `immediate`, `high`, `mid`, `low`, or `skip`.")
                    return True
                state["priority"] = token
            state["step"] = "topic"
            await update.message.reply_text("Step 3/5 - Topic(s)? comma-separated topic names or `skip`.")
            return True

        if step == "topic":
            if lowered not in {"skip", "none", "no"}:
                topic_text = ",".join(self.bot.reminder_logic_handler.split_topics(raw))
                topics = self.bot.reminder_logic_handler.split_topics(topic_text)
                if self.bot.settings.require_topic_on_add and not topics:
                    await update.message.reply_text(msg("error_topic_required"))
                    return True
                missing_topics = self.bot.db.has_missing_topics_for_chat(chat_id, topics)
                if missing_topics:
                    await update.message.reply_text(self.bot.reminder_logic_handler.format_missing_topics_message(chat_id, missing_topics))
                    return True
                state["topic"] = topic_text
            else:
                if self.bot.settings.require_topic_on_add:
                    await update.message.reply_text(msg("error_topic_required"))
                    return True
                state["topic"] = ""
            state["step"] = "recurrence"
            await update.message.reply_text("Step 4/5 - Repeat interval? `daily`, `weekly`, `monthly`, or `skip`.")
            return True

        if step == "recurrence":
            if lowered not in {"skip", "none", "no"}:
                token = lowered.strip()
                if token not in {"daily", "weekly", "monthly"}:
                    await update.message.reply_text("Invalid interval. Use `daily`, `weekly`, `monthly`, or `skip`.")
                    return True
                if not str(state.get("due_at_utc") or ""):
                    await update.message.reply_text(msg("error_recurrence_requires_due"))
                    return True
                state["recurrence"] = token
            else:
                state["recurrence"] = ""
            state["step"] = "extras"
            await update.message.reply_text("Step 5/5 - Add `link:<url>` and/or `notes:<text>`, or `skip`.")
            return True

        if step == "extras":
            if lowered not in {"skip", "none", "no"}:
                link_match = re.search(r"link\s*:\s*(\S+)", raw, re.IGNORECASE)
                notes_match = re.search(r"notes\s*:\s*(.+)$", raw, re.IGNORECASE)
                if link_match:
                    candidate = link_match.group(1).strip().rstrip(").,]")
                    if re.match(r"^https?://\S+$", candidate, re.IGNORECASE):
                        state["link"] = candidate
                elif re.match(r"^https?://\S+$", raw, re.IGNORECASE):
                    state["link"] = raw
                if notes_match:
                    state["notes"] = notes_match.group(1).strip()
                elif not state.get("link"):
                    state["notes"] = raw

            timezone_name = self.bot.settings.default_timezone
            user_id = self.bot.db.upsert_user(update.effective_user.id, update.effective_user.username, timezone_name)
            reminder_id = self.bot.db.create_reminder(
                user_id=user_id,
                source_message_id=None,
                source_kind="user_input",
                title=str(state.get("title") or "").strip(),
                topic=str(state.get("topic") or ""),
                notes=str(state.get("notes") or ""),
                link=str(state.get("link") or ""),
                priority=str(state.get("priority") or "mid"),
                due_at_utc=str(state.get("due_at_utc") or ""),
                timezone_name=timezone_name,
                chat_id_to_notify=chat_id,
                recurrence_rule=str(state.get("recurrence") or ""),
            )
            self.bot.db.set_reminder_topics_for_chat(reminder_id, chat_id, self.bot.reminder_logic_handler.split_topics(str(state.get("topic") or "")))
            self.bot.pending_add_wizards.pop(chat_id, None)
            await update.message.reply_text(
                format_reminder_brief(
                    reminder_id,
                    str(state.get("title") or ""),
                    str(state.get("due_at_utc") or ""),
                    self.bot.settings.default_timezone,
                )
            )
            await self.bot.calendar_sync_handler.sync_calendar_upsert(reminder_id)
            return True

        self.bot.pending_add_wizards.pop(chat_id, None)
        return False
