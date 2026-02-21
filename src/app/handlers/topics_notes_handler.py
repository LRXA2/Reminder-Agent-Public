from __future__ import annotations

from typing import TYPE_CHECKING

from telegram import Update
from telegram.ext import ContextTypes

from src.app.handlers.reminder_formatting import format_reminder_detail
from src.app.messages import msg

if TYPE_CHECKING:
    from src.app.reminder_bot import ReminderBot


class TopicsNotesHandler:
    def __init__(self, bot: "ReminderBot") -> None:
        self.bot = bot

    async def notes_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not update.message:
            return
        self.bot._clear_pending_flows(update.effective_chat.id, keep={"notes_wizard"})

        if context.args:
            try:
                reminder_id = int(context.args[0])
            except ValueError:
                await update.message.reply_text(msg("error_id_number"))
                return
            row = self.bot.db.get_reminder_by_id_for_chat(reminder_id, update.effective_chat.id)
            if row is None:
                await update.message.reply_text(msg("error_not_found", id=reminder_id))
                return
            notes = str(row["notes"] or "").strip()
            if not notes:
                await update.message.reply_text(msg("error_notes_empty_for_id", id=reminder_id))
                return
            await update.message.reply_text(format_reminder_detail(dict(row), self.bot.settings.default_timezone))
            return
        self.bot.pending_notes_wizards[update.effective_chat.id] = {"mode": "menu"}
        await update.message.reply_text(
            "Notes wizard. Choose: `list`, `view <id>`, `edit <id>`, `clear <id>`, or `cancel`.",
            reply_markup=self.bot._notes_wizard_keyboard(),
        )

    async def topics_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not update.message:
            return
        self.bot._clear_pending_flows(update.effective_chat.id, keep={"topics_wizard"})

        if not context.args:
            self.bot.pending_topics_wizards[update.effective_chat.id] = {"mode": "menu"}
            await update.message.reply_text(
                "Topics wizard. Choose: `list`, `list all`, `create <name>`, `rename <id> <new>`, `delete <id>`, `merge <from> <to>`, or `cancel`.",
                reply_markup=self.bot._topics_wizard_keyboard(),
            )
            return

        include_archived = False
        arg = context.args[0].strip().lower()
        if arg == "all":
            include_archived = True
        elif arg == "create":
            name = " ".join(context.args[1:]).strip()
            if not name:
                await update.message.reply_text(msg("usage_topics_create"))
                return
            self.bot.db.create_topic_for_chat(update.effective_chat.id, name)
            await update.message.reply_text(msg("status_topic_created", topic=name))
            return
        elif arg == "rename":
            if len(context.args) < 3:
                await update.message.reply_text(msg("usage_topics_rename"))
                return
            try:
                topic_id = int(context.args[1])
            except ValueError:
                await update.message.reply_text(msg("error_topic_id_number"))
                return
            new_name = " ".join(context.args[2:]).strip()
            if not new_name:
                await update.message.reply_text(msg("usage_topics_rename"))
                return
            ok = self.bot.db.rename_topic_for_chat(update.effective_chat.id, topic_id, new_name)
            if not ok:
                await update.message.reply_text(msg("error_topic_not_found", id=topic_id))
                return
            await update.message.reply_text(msg("status_topic_renamed", id=topic_id, topic=new_name))
            return
        elif arg == "delete":
            if len(context.args) != 2:
                await update.message.reply_text(msg("usage_topics_delete"))
                return
            try:
                topic_id = int(context.args[1])
            except ValueError:
                await update.message.reply_text(msg("error_topic_id_number"))
                return
            ok = self.bot.db.delete_topic_for_chat(update.effective_chat.id, topic_id)
            if not ok:
                await update.message.reply_text(msg("error_topic_not_found", id=topic_id))
                return
            await update.message.reply_text(msg("status_topic_deleted", id=topic_id))
            return
        elif arg == "merge":
            if len(context.args) != 3:
                await update.message.reply_text(msg("usage_topics_merge"))
                return
            try:
                from_id = int(context.args[1])
                to_id = int(context.args[2])
            except ValueError:
                await update.message.reply_text(msg("error_topic_id_number"))
                return
            ok = self.bot.db.merge_topics_for_chat(update.effective_chat.id, from_id, to_id)
            if not ok:
                await update.message.reply_text(msg("error_topics_merge_failed"))
                return
            await update.message.reply_text(msg("status_topics_merged", from_id=from_id, to_id=to_id))
            return
        else:
            await update.message.reply_text(msg("usage_topics"))
            return

        rows = self.bot.db.list_topic_index_for_chat(update.effective_chat.id, include_archived=include_archived)
        if not rows:
            await update.message.reply_text(msg("error_topics_empty"))
            return

        lines = ["Topics (open + archived):" if include_archived else "Topics (open only):"]
        for idx, row in enumerate(rows, start=1):
            topic_id = int(row["id"])
            topic = str(row["display_name"] or "").strip()
            internal_name = str(row["internal_name"] or "").strip()
            open_count = int(row["open_count"] or 0)
            archived_count = int(row["archived_count"] or 0)
            if include_archived:
                lines.append(f"{idx}) [{topic_id}] {topic} ({internal_name}) - open:{open_count}, archived:{archived_count}")
            else:
                lines.append(f"{idx}) [{topic_id}] {topic} - open:{open_count}")
        await update.message.reply_text("\n".join(lines))

    async def topic_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not update.message:
            return
        self.bot._clear_pending_flows(update.effective_chat.id)
        args = context.args or []
        if len(args) < 3:
            await update.message.reply_text(msg("usage_topic"))
            return
        action = args[0].strip().lower()
        try:
            reminder_id = int(args[1])
        except ValueError:
            await update.message.reply_text(msg("error_id_number"))
            return
        topic_name = " ".join(args[2:]).strip()
        if not topic_name:
            await update.message.reply_text(msg("usage_topic"))
            return
        existing = self.bot.db.get_reminder_by_id_for_chat(reminder_id, update.effective_chat.id)
        if existing is None:
            await update.message.reply_text(msg("error_not_found", id=reminder_id))
            return
        if action == "add":
            missing = self.bot.db.has_missing_topics_for_chat(update.effective_chat.id, [topic_name])
            if missing:
                await update.message.reply_text(self.bot._format_missing_topics_message(update.effective_chat.id, missing))
                return
            ok = self.bot.db.add_topic_to_reminder_for_chat(reminder_id, update.effective_chat.id, topic_name)
            if not ok:
                await update.message.reply_text(msg("error_update_failed", id=reminder_id))
                return
            await update.message.reply_text(msg("status_topic_added_to_reminder", topic=topic_name, id=reminder_id))
            return
        if action == "remove":
            ok = self.bot.db.remove_one_topic_from_reminder_for_chat(reminder_id, update.effective_chat.id, topic_name)
            if not ok:
                await update.message.reply_text(msg("error_topic_not_on_reminder", topic=topic_name, id=reminder_id))
                return
            await update.message.reply_text(msg("status_topic_removed_from_reminder", topic=topic_name, id=reminder_id))
            return
        await update.message.reply_text(msg("usage_topic"))
