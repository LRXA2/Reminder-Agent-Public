from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone

import dateparser
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from dateparser.search import search_dates
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from src.app.handlers.attachment_input_handler import AttachmentInputHandler
from src.app.handlers.reminder_draft_manager import ReminderDraftManager
from src.app.handlers.reminder_formatting import (
    format_reminder_brief,
    format_reminder_detail,
    format_reminder_list_item,
)
from src.app.handlers.text_input_handler import TextInputHandler
from src.clients.ollama_client import OllamaClient
from src.clients.stt_client import SttClient
from src.core.config import Settings
from src.storage.database import Database


LOGGER = logging.getLogger(__name__)

HELP_TEXT = """Reminder Bot Commands

/help
Show this help message

/add <task> [p:immediate|high|mid|low] [at:<time>] [every:daily|weekly|monthly]
Example: /add Pay rent p:high at:tomorrow 9am

/edit <id> [title:<text>] [p:<priority>] [at:<datetime>] [notes:<text>] [every:daily|weekly|monthly|none]
Examples:
- /edit 12 p:high at:tomorrow 9am
- /edit 12 title:Review ASAVC notes:Bring Smart No.4

/done <id>
Example: /done 12

/delete <id>
Permanently delete reminder

/detail <id>
Show full reminder details

/delete <id>
Permanently delete reminder (no archive)

/edit <id> [title:<text>] [p:<priority>] [at:<datetime>] [notes:<text>] [every:daily|weekly|monthly|none]
Edit reminder fields

/detail <id>
Show full reminder details

/list all
/list priority <immediate|high|mid|low>
/list due <Nd>
Example: /list due 14d
/list today
/list tomorrow
/list overdue

/summary
Summarize recent monitored group messages

You can also type natural language like:
- summarize for me
- help me summarize
- summarize it for me and remind me tomorrow 9am p:high
- reply to a message: add as reminder high tomorrow 9am
- reply to a reminder card: set to tomorrow 8am high
- what hackathons are available on 1 Mar - 15 Mar

Reminder response format:
- ID: <id>
- Title: <title>
- Date: dd/mm/yy [HH:mm]
Use /detail <id> for full notes/details.

/models
List installed Ollama models

/model
Show active text/vision models

/model <name>
Set active text model (backward-compatible)

/model text <name>
Set active text model

/model vision <name>
Set active vision model

/model tag <name> vision
Tag a model as vision-capable

/model untag <name> vision
Remove a model's vision-capable tag

/status
Show Ollama and GPU status

Image reminder flow:
- send an image
- add a caption with reminder details, or reply to the image (example: remind me high tomorrow 9am)

Audio reminder flow:
- send audio/voice with caption, or reply to audio/voice
- examples: summarize this audio / create reminders from this recording

Draft confirmation flow for summaries/images/documents:
- bot proposes one or more reminders first
- you review/edit before saving
- reply with: confirm | confirm 1,3 | edit <n> ... | remove <n> | cancel

Document flow (DOCX/PDF):
- send a DOCX or PDF with caption, or reply to one
- examples: summarize this document / create reminder low tomorrow 9am
"""


class ReminderBot:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.db = Database(settings.db_path)
        initial_text_model = settings.ollama_text_model or settings.ollama_model
        self.ollama = OllamaClient(
            settings.ollama_base_url,
            text_model=initial_text_model,
            vision_model=settings.ollama_vision_model,
        )
        saved_text_model = self.db.get_app_setting("ollama_text_model")
        legacy_model = self.db.get_app_setting("ollama_model")
        if saved_text_model:
            self.ollama.set_text_model(saved_text_model)
        elif legacy_model:
            self.ollama.set_text_model(legacy_model)
            self.db.set_app_setting("ollama_text_model", legacy_model)
        elif initial_text_model:
            self.db.set_app_setting("ollama_text_model", initial_text_model)

        saved_vision_model = self.db.get_app_setting("ollama_vision_model")
        if saved_vision_model:
            self.ollama.set_vision_model(saved_vision_model)
        elif settings.ollama_vision_model:
            self.db.set_app_setting("ollama_vision_model", settings.ollama_vision_model)

        self.vision_model_tags = self._load_vision_model_tags()
        current_vision = self.ollama.get_vision_model()
        if current_vision:
            self.vision_model_tags.add(current_vision)
            self._save_vision_model_tags()
        ollama_ready = self.ollama.ensure_server(
            autostart=settings.ollama_autostart,
            timeout_seconds=settings.ollama_start_timeout_seconds,
            use_highest_vram_gpu=settings.ollama_use_highest_vram_gpu,
        )
        if not ollama_ready:
            LOGGER.warning("Ollama is not reachable at %s", settings.ollama_base_url)
        self.scheduler = AsyncIOScheduler(timezone=self.settings.default_timezone)
        self.app = Application.builder().token(settings.telegram_bot_token).build()
        self.stt = SttClient(self.settings)
        self.reminder_draft_manager = ReminderDraftManager(self.db, self.ollama, self.settings)
        self.text_input_handler = TextInputHandler(self.db, self.ollama, self.settings, self.reminder_draft_manager)
        self.attachment_input_handler = AttachmentInputHandler(
            self.app,
            self.db,
            self.ollama,
            self.stt,
            self.settings,
            self.reminder_draft_manager,
        )
        self._register_handlers()
        self._register_jobs()

    def _register_handlers(self) -> None:
        self.app.add_handler(MessageHandler(filters.ALL, self._ingest_message), group=-1)
        self.app.add_handler(CommandHandler("help", self.help_command))
        self.app.add_handler(CommandHandler("add", self.add_command))
        self.app.add_handler(CommandHandler("edit", self.edit_command))
        self.app.add_handler(CommandHandler("done", self.done_command))
        self.app.add_handler(CommandHandler("delete", self.delete_command))
        self.app.add_handler(CommandHandler(["detail", "details"], self.detail_command))
        self.app.add_handler(CommandHandler("list", self.list_command))
        self.app.add_handler(CommandHandler("summary", self.summary_command))
        self.app.add_handler(CommandHandler("models", self.models_command))
        self.app.add_handler(CommandHandler("model", self.model_command))
        self.app.add_handler(CommandHandler("status", self.status_command))
        self.app.add_handler(
            MessageHandler(
                (filters.PHOTO | filters.Document.ALL | filters.AUDIO | filters.VOICE) & ~filters.COMMAND,
                self.attachment_message_handler,
            )
        )
        self.app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self.normal_chat_handler))

    def _register_jobs(self) -> None:
        self.scheduler.add_job(self.process_due_reminders, "interval", seconds=30)
        self.scheduler.add_job(self.cleanup_archives, "cron", hour=1, minute=0)
        self.scheduler.add_job(self.cleanup_messages, "cron", hour=1, minute=15)
        digest_times = self.settings.digest_times_local or (
            (self.settings.digest_hour_local, self.settings.digest_minute_local),
        )
        seen: set[tuple[int, int]] = set()
        for hour, minute in digest_times:
            if (hour, minute) in seen:
                continue
            seen.add((hour, minute))
            self.scheduler.add_job(
                self.send_daily_digest,
                "cron",
                hour=hour,
                minute=minute,
            )

    async def _ingest_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not update.message:
            return
        message = update.message
        text = message.text or message.caption or ""
        source_type = "group" if message.chat.type in {"group", "supergroup"} else "dm"
        if not self._should_store_message(message.chat_id, source_type, text):
            return
        received_at = message.date.astimezone(timezone.utc).isoformat()
        sender_id = message.from_user.id if message.from_user else None
        self.db.save_inbound_message(
            chat_id=message.chat_id,
            telegram_message_id=message.message_id,
            sender_telegram_id=sender_id,
            text=text,
            chat_type=message.chat.type,
            source_type=source_type,
            received_at_utc=received_at,
        )

    def _should_store_message(self, chat_id: int, source_type: str, text: str) -> bool:
        normalized = (text or "").strip().lower()

        if source_type == "group":
            monitored_group = self.settings.monitored_group_chat_id
            if not monitored_group:
                return False
            return int(chat_id) == int(monitored_group)

        if int(chat_id) != int(self.settings.personal_chat_id):
            return False

        hackathon_markers = ("hackathon", "hackathons", "devpost", "mlh", "registration", "deadline")
        return any(marker in normalized for marker in hackathon_markers)

    async def help_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if update.message:
            await update.message.reply_text(HELP_TEXT)

    async def add_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not update.message or not update.effective_user:
            return
        raw = " ".join(context.args).strip()
        if not raw:
            await update.message.reply_text("Usage: /add <task> p:high at:tomorrow 9am")
            return

        parsed = self._parse_add_payload(raw)
        if parsed.get("error"):
            await update.message.reply_text(parsed["error"])
            return

        timezone_name = self.settings.default_timezone
        user_id = self.db.upsert_user(update.effective_user.id, update.effective_user.username, timezone_name)

        reminder_id = self.db.create_reminder(
            user_id=user_id,
            source_message_id=None,
            source_kind="user_input",
            title=parsed["title"],
            notes="",
            priority=parsed["priority"],
            due_at_utc=parsed["due_at_utc"],
            timezone_name=timezone_name,
            chat_id_to_notify=update.effective_chat.id,
            recurrence_rule=parsed["recurrence"],
        )
        await update.message.reply_text(
            format_reminder_brief(reminder_id, parsed["title"], parsed["due_at_utc"], self.settings.default_timezone)
        )

    async def done_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not update.message:
            return
        if not context.args:
            await update.message.reply_text("Usage: /done <id>")
            return
        try:
            reminder_id = int(context.args[0])
        except ValueError:
            await update.message.reply_text("Reminder id must be a number.")
            return

        ok = self.db.mark_done_and_archive(reminder_id)
        if ok:
            await update.message.reply_text(f"Reminder #{reminder_id} archived.")
        else:
            await update.message.reply_text(f"Reminder #{reminder_id} not found or already archived.")

    async def edit_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not update.message:
            return
        if len(context.args) < 2:
            await update.message.reply_text(
                "Usage: /edit <id> [title:<text>] [p:<priority>] [at:<datetime>] [notes:<text>] [every:daily|weekly|monthly|none]"
            )
            return

        try:
            reminder_id = int(context.args[0])
        except ValueError:
            await update.message.reply_text("Reminder id must be a number.")
            return

        existing = self.db.get_reminder_by_id(reminder_id)
        if existing is None:
            await update.message.reply_text(f"Reminder #{reminder_id} not found.")
            return

        payload = " ".join(context.args[1:]).strip()
        if not payload:
            await update.message.reply_text("Please provide fields to update.")
            return

        parsed = self._parse_edit_payload(payload)
        if parsed.get("error"):
            await update.message.reply_text(parsed["error"])
            return

        current = dict(existing)
        title = parsed["title"] if parsed["title"] is not None else str(current.get("title") or "")
        notes = parsed["notes"] if parsed["notes"] is not None else str(current.get("notes") or "")
        priority = parsed["priority"] if parsed["priority"] is not None else str(current.get("priority") or "mid")
        due_at_utc = parsed["due_at_utc"] if parsed["due_at_utc"] is not None else str(current.get("due_at_utc") or "")

        existing_recurrence = current.get("recurrence_rule")
        recurrence_rule: str | None
        if parsed["recurrence"] is None:
            recurrence_rule = str(existing_recurrence) if existing_recurrence is not None else None
        else:
            recurrence_rule = parsed["recurrence"]

        if not title.strip():
            await update.message.reply_text("Title cannot be empty.")
            return
        if not due_at_utc:
            await update.message.reply_text("Due date/time cannot be empty.")
            return

        ok = self.db.update_reminder_fields(
            reminder_id=reminder_id,
            title=title.strip(),
            notes=notes,
            priority=priority,
            due_at_utc=due_at_utc,
            recurrence_rule=recurrence_rule,
        )
        if not ok:
            await update.message.reply_text(f"Reminder #{reminder_id} could not be updated.")
            return

        await update.message.reply_text(
            format_reminder_brief(reminder_id, title, due_at_utc, self.settings.default_timezone)
        )

    async def delete_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not update.message:
            return
        if not context.args:
            await update.message.reply_text("Usage: /delete <id>")
            return
        try:
            reminder_id = int(context.args[0])
        except ValueError:
            await update.message.reply_text("Reminder id must be a number.")
            return

        ok = self.db.delete_reminder_permanently(reminder_id)
        if ok:
            await update.message.reply_text(f"Reminder #{reminder_id} permanently deleted.")
        else:
            await update.message.reply_text(f"Reminder #{reminder_id} not found.")

    async def detail_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not update.message:
            return
        if not context.args:
            await update.message.reply_text("Usage: /detail <id>")
            return
        try:
            reminder_id = int(context.args[0])
        except ValueError:
            await update.message.reply_text("Reminder id must be a number.")
            return

        row = self.db.get_reminder_by_id(reminder_id)
        if row is None:
            await update.message.reply_text(f"Reminder #{reminder_id} not found.")
            return
        await update.message.reply_text(format_reminder_detail(dict(row), self.settings.default_timezone))

    async def list_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not update.message:
            return

        if not context.args:
            await update.message.reply_text("Usage: /list all | /list priority high | /list due 14d")
            return

        mode = context.args[0].lower()
        rows = []
        try:
            if mode == "all":
                rows = self.db.list_reminders("all")
            elif mode == "priority" and len(context.args) >= 2:
                rows = self.db.list_reminders("priority", context.args[1].lower())
            elif mode == "due" and len(context.args) >= 2:
                value = context.args[1].lower().strip()
                if not value.endswith("d"):
                    await update.message.reply_text("Use day format like: /list due 14d")
                    return
                rows = self.db.list_reminders("due_days", value[:-1])
            elif mode in {"today", "tomorrow", "overdue"}:
                rows = self.db.list_reminders(mode)
            else:
                await update.message.reply_text("Unknown list filter. Try /help")
                return
        except ValueError:
            await update.message.reply_text("Invalid list value. Try /help")
            return

        if not rows:
            await update.message.reply_text("No matching open reminders.")
            return

        lines = ["Open reminders:"]
        for idx, row in enumerate(rows[:30], start=1):
            lines.append(format_reminder_list_item(idx, dict(row), self.settings.default_timezone))
        if len(rows) > 30:
            lines.append(f"...and {len(rows) - 30} more. Use /list due 14d or /list priority high to narrow.")
        await update.message.reply_text("\n\n".join(lines))

    async def summary_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not update.message:
            return

        if not self.settings.monitored_group_chat_id:
            await update.message.reply_text("MONITORED_GROUP_CHAT_ID is not set.")
            return

        await update.message.reply_text("Got it - summarizing now...")
        try:
            summary = await self._build_group_summary()
            await update.message.reply_text(summary)
            await update.message.reply_text("Summary complete. I will now draft reminder suggestions.")
            await self.reminder_draft_manager.propose_from_text(
                update=update,
                source_kind="group_summary",
                content=summary,
                user_instruction="/summary",
            )
        except Exception as exc:
            await update.message.reply_text(f"I hit an error while running summary: {exc}")

    async def models_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not update.message:
            return
        models = self.ollama.list_models()
        if not models:
            await update.message.reply_text("No Ollama models found. Pull one with: ollama pull <model>")
            return

        active_text = self.ollama.get_text_model()
        active_vision = self.ollama.get_vision_model()
        lines = ["Installed Ollama models:"]
        for model in models:
            markers: list[str] = []
            if model == active_text:
                markers.append("text")
            if model == active_vision:
                markers.append("vision-active")
            if model in self.vision_model_tags:
                markers.append("vision")
            marker_text = f" ({', '.join(markers)})" if markers else ""
            lines.append(f"- {model}{marker_text}")
        await update.message.reply_text("\n".join(lines))

    async def model_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not update.message:
            return

        if not context.args:
            text_model = self.ollama.get_text_model() or "(none)"
            vision_model = self.ollama.get_vision_model() or "(none)"
            await update.message.reply_text(
                "Active models:\n"
                f"- text: {text_model}\n"
                f"- vision: {vision_model}\n\n"
                "Usage:\n"
                "- /model <name>\n"
                "- /model text <name>\n"
                "- /model vision <name>\n"
                "- /model tag <name> vision\n"
                "- /model untag <name> vision"
            )
            return

        models = self.ollama.list_models()
        first = context.args[0].lower()

        if first in {"tag", "untag"}:
            if len(context.args) < 3 or context.args[-1].lower() != "vision":
                await update.message.reply_text("Usage: /model tag <name> vision OR /model untag <name> vision")
                return
            target = " ".join(context.args[1:-1]).strip()
            if target not in models:
                await update.message.reply_text(f"Model not installed. Run: ollama pull {target}")
                return
            if first == "tag":
                self.vision_model_tags.add(target)
                self._save_vision_model_tags()
                await update.message.reply_text(f"Tagged as vision-capable: {target}")
            else:
                self.vision_model_tags.discard(target)
                self._save_vision_model_tags()
                await update.message.reply_text(f"Removed vision tag: {target}")
            return

        target_role = "text"
        if first in {"text", "vision"}:
            target_role = first
            chosen = " ".join(context.args[1:]).strip()
            if not chosen:
                await update.message.reply_text(f"Usage: /model {target_role} <name>")
                return
        else:
            chosen = " ".join(context.args).strip()

        if chosen not in models:
            await update.message.reply_text(f"Model not installed. Run: ollama pull {chosen}")
            return

        if target_role == "vision":
            self.ollama.set_vision_model(chosen)
            self.db.set_app_setting("ollama_vision_model", chosen)
            self.vision_model_tags.add(chosen)
            self._save_vision_model_tags()
            await update.message.reply_text(f"Active vision model set to: {chosen}")
            return

        self.ollama.set_text_model(chosen)
        self.db.set_app_setting("ollama_text_model", chosen)
        self.db.set_app_setting("ollama_model", chosen)
        await update.message.reply_text(f"Active text model set to: {chosen}")

    async def status_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not update.message:
            return

        ollama_ready = self.ollama.ensure_server(
            autostart=False,
            timeout_seconds=2,
            use_highest_vram_gpu=False,
        )
        text_model = self.ollama.get_text_model() or "(none)"
        vision_model = self.ollama.get_vision_model() or "(none)"
        gpu = self.ollama.detect_nvidia_gpu()
        ps_output = self.ollama.ollama_ps()

        lines = [
            f"Ollama server: {'running' if ollama_ready else 'not reachable'}",
            f"Active text model: {text_model}",
            f"Active vision model: {vision_model}",
        ]

        if gpu.get("has_gpu"):
            lines.append("Nvidia GPU: " + ", ".join(gpu.get("gpus", [])))
        else:
            lines.append("Nvidia GPU: not detected")

        lines.append("ollama ps:")
        lines.append(ps_output)
        await update.message.reply_text("\n".join(lines))

    async def normal_chat_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not update.message:
            return
        text = (update.message.text or "").strip()
        if not text:
            return

        handled_draft_followup = await self.reminder_draft_manager.handle_followup(update, text)
        if handled_draft_followup:
            return

        handled_attachment_reply = await self.attachment_input_handler.handle_message(
            update,
            text,
            allow_current_attachment=False,
        )
        if handled_attachment_reply:
            return

        await self.text_input_handler.handle_message(
            update,
            parse_add_payload=self._parse_add_payload,
            build_group_summary=self._build_group_summary,
        )

    async def attachment_message_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not update.message:
            return
        if update.effective_chat.id != self.settings.personal_chat_id:
            return

        caption = (update.message.caption or "").strip()
        if not caption:
            return

        await self.attachment_input_handler.handle_message(
            update,
            caption,
            allow_current_attachment=True,
        )

    def _load_vision_model_tags(self) -> set[str]:
        raw = self.db.get_app_setting("ollama_vision_tags") or ""
        tags = {part.strip() for part in raw.split(",") if part.strip()}
        return tags

    def _save_vision_model_tags(self) -> None:
        serialized = ",".join(sorted(self.vision_model_tags))
        self.db.set_app_setting("ollama_vision_tags", serialized)

    async def process_due_reminders(self) -> None:
        now_iso = datetime.now(timezone.utc).isoformat()
        rows = self.db.get_due_reminders(now_iso)
        for row in rows:
            chat_id = int(row["chat_id_to_notify"])
            try:
                await self.app.bot.send_message(
                    chat_id=chat_id,
                    text=f"🔔 Reminder #{row['id']}: {row['title']} ({row['priority']})",
                )
            except Exception as exc:
                LOGGER.exception("Failed to send reminder %s: %s", row["id"], exc)
                continue
            self.db.mark_reminder_notified(int(row["id"]), row["due_at_utc"])

            recurrence = (row["recurrence_rule"] or "").strip().lower()
            if recurrence:
                next_due = self._compute_next_due(row["due_at_utc"], recurrence)
                if next_due:
                    self.db.update_recurring_due(int(row["id"]), next_due)

    async def cleanup_archives(self) -> None:
        deleted = self.db.delete_old_archived(self.settings.archive_retention_days)
        if deleted:
            LOGGER.info("Deleted %s archived reminders older than retention", deleted)

    async def cleanup_messages(self) -> None:
        retention_days = self.settings.message_retention_days
        if retention_days <= 0:
            return
        deleted = self.db.delete_old_messages(retention_days)
        if deleted:
            LOGGER.info("Deleted %s stored messages older than %s days", deleted, retention_days)

    async def send_daily_digest(self) -> None:
        if not self.settings.personal_chat_id:
            return
        lines = ["Daily digest"]
        all_items = self.db.list_reminders("all")
        if all_items:
            lines.append("All open reminders:")
            for idx, row in enumerate(all_items[:20], start=1):
                lines.append(format_reminder_list_item(idx, dict(row), self.settings.default_timezone))
            if len(all_items) > 20:
                lines.append(f"...and {len(all_items) - 20} more.")
        else:
            lines.append("All open reminders: none")

        if self.settings.monitored_group_chat_id:
            summary = await self._build_group_summary(save=False)
            lines.append("Group summary:")
            lines.append(summary)

        await self.app.bot.send_message(chat_id=self.settings.personal_chat_id, text="\n".join(lines))

    async def _build_group_summary(self, save: bool = True) -> str:
        rows = self.db.fetch_recent_group_messages(self.settings.monitored_group_chat_id, limit=50)
        if not rows:
            return "No recent messages in monitored group."

        lines = []
        for row in reversed(rows):
            text = (row["text"] or "").strip()
            if not text:
                continue
            if len(text) > 500:
                text = text[:500] + "..."
            lines.append(f"[{row['received_at_utc']}] {text}")

        summary = self.ollama.summarize_messages(lines)
        if save:
            now = datetime.now(timezone.utc)
            window_start = (now - timedelta(hours=24)).isoformat()
            self.db.save_summary(
                self.settings.monitored_group_chat_id,
                window_start,
                now.isoformat(),
                summary,
            )
        return summary

    def _parse_add_payload(self, payload: str) -> dict[str, str]:
        text = payload.strip()

        priority_match = re.search(r"(?:p|priority)\s*:\s*(immediate|high|mid|low)\b", text, re.IGNORECASE)
        priority = priority_match.group(1).lower() if priority_match else "mid"
        if priority_match:
            text = text[: priority_match.start()] + text[priority_match.end() :]

        recur_match = re.search(r"every\s*:\s*(daily|weekly|monthly)\b", text, re.IGNORECASE)
        recurrence = recur_match.group(1).lower() if recur_match else ""
        if recur_match:
            text = text[: recur_match.start()] + text[recur_match.end() :]

        due_dt = None
        at_match = re.search(r"at\s*:\s*(.+)$", text, re.IGNORECASE)
        if at_match:
            dt_text = at_match.group(1).strip()
            due_dt = dateparser.parse(
                dt_text,
                settings={
                    "TIMEZONE": self.settings.default_timezone,
                    "RETURN_AS_TIMEZONE_AWARE": True,
                    "PREFER_DATES_FROM": "future",
                },
            )
            text = text[: at_match.start()].strip()
        else:
            found = search_dates(
                text,
                settings={
                    "TIMEZONE": self.settings.default_timezone,
                    "RETURN_AS_TIMEZONE_AWARE": True,
                    "PREFER_DATES_FROM": "future",
                },
            )
            if found:
                dt_phrase, dt_value = found[-1]
                due_dt = dt_value
                text = text.replace(dt_phrase, " ").strip()

        cleaned = re.sub(r"\s+", " ", text).strip(" -")
        cleaned = re.sub(r"^(remind me to|remind me|todo)\s+", "", cleaned, flags=re.IGNORECASE).strip()

        if not cleaned:
            return {"error": "Missing reminder title. Example: /add Pay rent at:tomorrow 9am"}

        if due_dt is None:
            return {"error": "Missing or invalid date/time. Example: /add Pay rent at:tomorrow 9am"}

        due_utc = due_dt.astimezone(timezone.utc).isoformat()
        return {
            "title": cleaned,
            "priority": priority,
            "due_at_utc": due_utc,
            "recurrence": recurrence,
        }

    def _parse_edit_payload(self, payload: str) -> dict[str, str | None]:
        text = payload.strip()

        title: str | None = None
        notes: str | None = None
        priority: str | None = None
        due_at_utc: str | None = None
        recurrence: str | None = None

        title_match = re.search(r"title\s*:\s*(.+?)(?=\s+(?:notes|p|priority|at|every)\s*:|$)", text, re.IGNORECASE)
        if title_match:
            title = title_match.group(1).strip()

        notes_match = re.search(r"notes\s*:\s*(.+?)(?=\s+(?:title|p|priority|at|every)\s*:|$)", text, re.IGNORECASE)
        if notes_match:
            notes = notes_match.group(1).strip()

        priority_match = re.search(r"(?:p|priority)\s*:\s*(immediate|high|mid|low)\b", text, re.IGNORECASE)
        if priority_match:
            priority = priority_match.group(1).lower()

        recur_match = re.search(r"every\s*:\s*(daily|weekly|monthly|none)\b", text, re.IGNORECASE)
        if recur_match:
            recurrence = recur_match.group(1).lower()
            if recurrence == "none":
                recurrence = ""

        at_match = re.search(r"at\s*:\s*(.+?)(?=\s+(?:title|notes|p|priority|every)\s*:|$)", text, re.IGNORECASE)
        if at_match:
            dt_text = at_match.group(1).strip()
            due_dt = dateparser.parse(
                dt_text,
                settings={
                    "TIMEZONE": self.settings.default_timezone,
                    "RETURN_AS_TIMEZONE_AWARE": True,
                    "PREFER_DATES_FROM": "future",
                },
            )
            if due_dt is None:
                return {"error": "Invalid date/time in at:. Example: at:tomorrow 9am"}
            due_at_utc = due_dt.astimezone(timezone.utc).isoformat()

        if not any(value is not None for value in (title, notes, priority, due_at_utc, recurrence)):
            # Backward-compatible shorthand: /edit <id> <new title>
            title = text

        return {
            "title": title,
            "notes": notes,
            "priority": priority,
            "due_at_utc": due_at_utc,
            "recurrence": recurrence,
            "error": None,
        }

    def _compute_next_due(self, due_at_utc: str, recurrence: str) -> str | None:
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

    def run_polling(self) -> None:
        logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s - %(message)s")
        logging.getLogger("httpx").setLevel(logging.WARNING)
        logging.getLogger("httpcore").setLevel(logging.WARNING)
        logging.getLogger("telegram").setLevel(logging.WARNING)
        self.scheduler.start()
        self.app.run_polling(drop_pending_updates=True)
