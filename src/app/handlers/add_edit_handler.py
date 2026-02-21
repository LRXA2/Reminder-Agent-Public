from __future__ import annotations

import re
from datetime import timezone
from typing import TYPE_CHECKING

from telegram import Update
from telegram.ext import ContextTypes

from src.app.handlers.reminder_formatting import format_due_display, format_reminder_brief
from src.app.messages import msg

if TYPE_CHECKING:
    from src.app.reminder_bot import ReminderBot


class AddEditHandler:
    def __init__(self, bot: "ReminderBot") -> None:
        self.bot = bot

    async def add_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not update.message or not update.effective_user:
            return
        self.bot._clear_pending_flows(update.effective_chat.id, keep={"add_wizard", "add_confirm"})
        args = context.args or []
        raw = " ".join(args).strip()
        if not raw:
            await update.message.reply_text(msg("usage_add"))
            return

        if not self.bot._looks_like_inline_add_payload(raw):
            self.bot.pending_add_wizards[update.effective_chat.id] = {
                "title": raw,
                "due_at_utc": "",
                "priority": "mid",
                "topic": "",
                "recurrence": "",
                "link": "",
                "notes": "",
                "step": "due",
            }
            await update.message.reply_text(
                "Got it. Step 1/5 - Add due date/time (e.g. `tomorrow 9am`) or `skip` for no due."
            )
            return

        parsed = self.bot._parse_add_payload(raw)
        if parsed.get("error"):
            await update.message.reply_text(parsed["error"])
            return

        topics = self.bot._split_topics(parsed.get("topic", ""))
        if self.bot.settings.require_topic_on_add and not topics:
            await update.message.reply_text(msg("error_topic_required"))
            return
        missing_topics = self.bot.db.has_missing_topics_for_chat(update.effective_chat.id, topics)
        if missing_topics:
            await update.message.reply_text(self.bot._format_missing_topics_message(update.effective_chat.id, missing_topics))
            return

        if parsed.get("needs_confirmation"):
            queue = self.bot.pending_add_confirmations.setdefault(update.effective_chat.id, [])
            queue.append(
                {
                    "title": parsed["title"],
                    "topic": parsed.get("topic", ""),
                    "priority": parsed["priority"],
                    "due_at_utc": parsed["due_at_utc"],
                    "recurrence": parsed["recurrence"],
                    "link": parsed.get("link", ""),
                }
            )
            due_local = format_due_display(parsed["due_at_utc"], self.bot.settings.default_timezone)
            await update.message.reply_text(
                msg("status_due_guess", due_local=due_local, timezone=self.bot.settings.default_timezone)
            )
            return

        timezone_name = self.bot.settings.default_timezone
        user_id = self.bot.db.upsert_user(update.effective_user.id, update.effective_user.username, timezone_name)

        reminder_id = self.bot.db.create_reminder(
            user_id=user_id,
            source_message_id=None,
            source_kind="user_input",
            title=parsed["title"],
            topic=parsed.get("topic", ""),
            notes="",
            link=parsed["link"],
            priority=parsed["priority"],
            due_at_utc=parsed["due_at_utc"],
            timezone_name=timezone_name,
            chat_id_to_notify=update.effective_chat.id,
            recurrence_rule=parsed["recurrence"],
        )
        self.bot.db.set_reminder_topics_for_chat(reminder_id, update.effective_chat.id, topics)
        await update.message.reply_text(
            format_reminder_brief(reminder_id, parsed["title"], parsed["due_at_utc"], self.bot.settings.default_timezone)
        )
        await self.bot._sync_calendar_upsert(reminder_id)

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
                due_dt, _conf = self.bot._parse_natural_datetime(raw)
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
                topic_text = ",".join(self.bot._split_topics(raw))
                topics = self.bot._split_topics(topic_text)
                if self.bot.settings.require_topic_on_add and not topics:
                    await update.message.reply_text(msg("error_topic_required"))
                    return True
                missing_topics = self.bot.db.has_missing_topics_for_chat(chat_id, topics)
                if missing_topics:
                    await update.message.reply_text(self.bot._format_missing_topics_message(chat_id, missing_topics))
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
            self.bot.db.set_reminder_topics_for_chat(reminder_id, chat_id, self.bot._split_topics(str(state.get("topic") or "")))
            self.bot.pending_add_wizards.pop(chat_id, None)
            await update.message.reply_text(
                format_reminder_brief(
                    reminder_id,
                    str(state.get("title") or ""),
                    str(state.get("due_at_utc") or ""),
                    self.bot.settings.default_timezone,
                )
            )
            await self.bot._sync_calendar_upsert(reminder_id)
            return True

        self.bot.pending_add_wizards.pop(chat_id, None)
        return False

    async def edit_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not update.message:
            return
        self.bot._clear_pending_flows(update.effective_chat.id, keep={"edit_wizard"})
        args = context.args or []
        if len(args) == 1:
            try:
                reminder_id = int(args[0])
            except ValueError:
                await update.message.reply_text(msg("error_id_number"))
                return
            existing = self.bot.db.get_reminder_by_id_for_chat(reminder_id, update.effective_chat.id)
            if existing is None:
                await update.message.reply_text(msg("error_not_found", id=reminder_id))
                return
            row = dict(existing)
            self.bot.pending_edit_wizards[update.effective_chat.id] = {
                "id": str(reminder_id),
                "title": str(row.get("title") or ""),
                "due_at_utc": str(row.get("due_at_utc") or ""),
                "priority": str(row.get("priority") or "mid"),
                "topic": str(row.get("topics_text") or row.get("topic") or ""),
                "recurrence": str(row.get("recurrence_rule") or ""),
                "link": str(row.get("link") or ""),
                "notes": str(row.get("notes") or ""),
                "mode": "menu",
            }
            await update.message.reply_text(
                self.bot._render_edit_wizard_menu(self.bot.pending_edit_wizards[update.effective_chat.id]),
                reply_markup=self.bot._edit_wizard_keyboard(),
            )
            return
        if len(args) < 2:
            await update.message.reply_text(msg("usage_edit"))
            return

        try:
            reminder_id = int(args[0])
        except ValueError:
            await update.message.reply_text(msg("error_id_number"))
            return

        existing = self.bot.db.get_reminder_by_id_for_chat(reminder_id, update.effective_chat.id)
        if existing is None:
            await update.message.reply_text(msg("error_not_found", id=reminder_id))
            return

        payload = " ".join(args[1:]).strip()
        if not payload:
            await update.message.reply_text(msg("error_edit_no_fields"))
            return

        parsed = self.bot._parse_edit_payload(payload)
        if parsed.get("error"):
            await update.message.reply_text(str(parsed["error"]))
            return

        current = dict(existing)
        parsed_title = parsed.get("title")
        parsed_notes = parsed.get("notes")
        parsed_link = parsed.get("link")
        parsed_priority = parsed.get("priority")
        parsed_due = parsed.get("due_at_utc")

        title = str(parsed_title) if parsed_title is not None else str(current.get("title") or "")
        notes = str(parsed_notes) if parsed_notes is not None else str(current.get("notes") or "")
        link = str(parsed_link) if parsed_link is not None else str(current.get("link") or "")
        priority = str(parsed_priority) if parsed_priority is not None else str(current.get("priority") or "mid")
        due_at_utc = str(parsed_due) if parsed_due is not None else str(current.get("due_at_utc") or "")

        existing_recurrence = current.get("recurrence_rule")
        recurrence_rule: str | None
        parsed_recurrence = parsed.get("recurrence")
        if parsed_recurrence is None:
            recurrence_rule = str(existing_recurrence) if existing_recurrence is not None else None
        else:
            recurrence_rule = str(parsed_recurrence)

        if not title.strip():
            await update.message.reply_text(msg("error_title_empty"))
            return
        if not due_at_utc and recurrence_rule:
            await update.message.reply_text(msg("error_recurrence_requires_due"))
            return

        ok = self.bot.db.update_reminder_fields_for_chat(
            reminder_id=reminder_id,
            chat_id_to_notify=update.effective_chat.id,
            title=title.strip(),
            topic=str(current.get("topic") or ""),
            notes=notes,
            link=link,
            priority=priority,
            due_at_utc=due_at_utc,
            recurrence_rule=recurrence_rule,
        )
        if not ok:
            await update.message.reply_text(msg("error_update_failed", id=reminder_id))
            return

        topic_mode = str(parsed.get("topic_mode") or "")
        raw_topic_values = parsed.get("topic_values")
        topic_values = raw_topic_values if isinstance(raw_topic_values, list) else []
        if topic_mode == "clear":
            self.bot.db.set_reminder_topics_for_chat(reminder_id, update.effective_chat.id, [])
        elif topic_mode in {"replace", "add"}:
            missing_topics = self.bot.db.has_missing_topics_for_chat(update.effective_chat.id, topic_values)
            if missing_topics:
                await update.message.reply_text(msg("error_topics_missing_create", topics=", ".join(missing_topics)))
                return
            if topic_mode == "replace":
                self.bot.db.set_reminder_topics_for_chat(reminder_id, update.effective_chat.id, topic_values)
            else:
                for topic_name in topic_values:
                    self.bot.db.add_topic_to_reminder_for_chat(reminder_id, update.effective_chat.id, topic_name)
        elif topic_mode == "remove":
            for topic_name in topic_values:
                self.bot.db.remove_one_topic_from_reminder_for_chat(reminder_id, update.effective_chat.id, topic_name)

        await update.message.reply_text(
            format_reminder_brief(reminder_id, title, due_at_utc, self.bot.settings.default_timezone)
        )
        await self.bot._sync_calendar_upsert(reminder_id)
