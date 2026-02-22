from __future__ import annotations

from typing import TYPE_CHECKING

from telegram import Update
from telegram.ext import ContextTypes

from src.app.handlers.reminder_formatting import format_due_display, format_reminder_brief
from src.app.messages import msg

from .parsing import AddEditPayloadParser

if TYPE_CHECKING:
    from src.app.bot_orchestrator import ReminderBot


class AddEditCommandsFlow:
    def __init__(self, bot: "ReminderBot", parser: AddEditPayloadParser) -> None:
        self.bot = bot
        self.parser = parser

    async def add_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not update.message or not update.effective_user:
            return
        self.bot.flow_state_service.clear_pending_flows(update.effective_chat.id, keep={"add_wizard", "add_confirm"})
        args = context.args or []
        raw = " ".join(args).strip()
        if not raw:
            await update.message.reply_text(msg("usage_add"))
            return

        if not self.bot.reminder_logic_handler.looks_like_inline_add_payload(raw):
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

        parsed = self.parser.parse_add_payload(raw)
        if parsed.get("error"):
            await update.message.reply_text(parsed["error"])
            return

        topics = self.bot.reminder_logic_handler.split_topics(parsed.get("topic", ""))
        if self.bot.settings.require_topic_on_add and not topics:
            await update.message.reply_text(msg("error_topic_required"))
            return
        missing_topics = self.bot.db.has_missing_topics_for_chat(update.effective_chat.id, topics)
        if missing_topics:
            await update.message.reply_text(self.bot.reminder_logic_handler.format_missing_topics_message(update.effective_chat.id, missing_topics))
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
        await self.bot.calendar_sync_handler.sync_calendar_upsert(reminder_id)

    async def edit_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not update.message:
            return
        self.bot.flow_state_service.clear_pending_flows(update.effective_chat.id, keep={"edit_wizard"})
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
                self.bot.ui_wizard_handler._render_edit_wizard_menu(self.bot.pending_edit_wizards[update.effective_chat.id]),
                reply_markup=self.bot.ui_wizard_handler._edit_wizard_keyboard(),
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

        parsed = self.parser.parse_edit_payload(payload)
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
        await self.bot.calendar_sync_handler.sync_calendar_upsert(reminder_id)
