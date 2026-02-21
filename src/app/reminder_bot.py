from __future__ import annotations

import asyncio
import json
import logging
import re
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import dateparser
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from src.app.handlers.attachment_input import AttachmentInputHandler
from src.app.handlers.add_edit_handler import AddEditHandler
from src.app.handlers.chat_pipeline import ChatPipelineHandler
from src.app.handlers.datetime_parser import parse_datetime_text
from src.app.handlers.flow_state_service import FlowStateService
from src.app.handlers.job_runner import JobRunner
from src.app.handlers.list_sync_model_handler import ListSyncModelHandler
from src.app.handlers.reminder_draft import ReminderDraftManager
from src.app.handlers.reminder_formatting import (
    format_due_display,
    format_reminder_brief,
)
from src.app.handlers.text_input import TextInputHandler
from src.app.handlers.topics_notes_handler import TopicsNotesHandler
from src.app.handlers.wizards import UiWizardHandler
from src.app.handlers.summary_status_handler import SummaryStatusHandler
from src.app.messages import HELP_TEXT, HELP_TOPICS, msg
from src.app.prompts import datetime_fallback_prompt
from src.clients.ollama_client import OllamaClient
from src.clients.stt_client import SttClient
from src.core.config import Settings
from src.integrations.google_calendar_service import GoogleCalendarSyncService
from src.storage.database import Database
from src.userbot import UserbotIngestService


LOGGER = logging.getLogger(__name__)


class ReminderBot:
    LONG_SUMMARY_NOTES_THRESHOLD = 250

    def __init__(self, settings: Settings):
        self.settings = settings
        self.db = Database(settings.db_path)
        initial_text_model = settings.ollama_text_model or settings.ollama_model
        self.ollama = OllamaClient(
            settings.ollama_base_url,
            text_model=initial_text_model,
            vision_model=settings.ollama_vision_model,
            request_timeout_seconds=settings.ollama_request_timeout_seconds,
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
        self._gpu_task_lock = asyncio.Lock()
        self.scheduler = AsyncIOScheduler(timezone=self.settings.default_timezone)
        self.app = Application.builder().token(settings.telegram_bot_token).build()
        self.stt = SttClient(self.settings)
        self.userbot_ingest = UserbotIngestService(self.settings, self.db)
        self.calendar_sync = GoogleCalendarSyncService(self.settings, self.db)
        self.pending_add_confirmations: dict[int, list[dict[str, str]]] = {}
        self.pending_add_wizards: dict[int, dict[str, str]] = {}
        self.pending_edit_wizards: dict[int, dict[str, str]] = {}
        self.pending_model_wizards: dict[int, dict[str, str]] = {}
        self.pending_topics_wizards: dict[int, dict[str, str]] = {}
        self.pending_notes_wizards: dict[int, dict[str, str]] = {}
        self.pending_delete_wizards: dict[int, dict[str, str]] = {}
        self.reminder_draft_manager = ReminderDraftManager(
            self.db,
            self.ollama,
            self.settings,
            run_gpu_task=self.run_gpu_task,
            on_reminder_created=self._sync_calendar_upsert,
        )
        self.text_input_handler = TextInputHandler(
            self.db,
            self.ollama,
            self.settings,
            self.reminder_draft_manager,
            run_gpu_task=self.run_gpu_task,
            on_reminder_created=self._sync_calendar_upsert,
            on_reminder_updated=self._sync_calendar_upsert,
        )
        self.attachment_input_handler = AttachmentInputHandler(
            self.app,
            self.db,
            self.ollama,
            self.stt,
            self.settings,
            self.reminder_draft_manager,
            run_gpu_task=self.run_gpu_task,
        )
        self.add_edit_handler = AddEditHandler(self)
        self.chat_pipeline_handler = ChatPipelineHandler(self)
        self.flow_state_service = FlowStateService(self)
        self.job_runner = JobRunner(self)
        self.list_sync_model_handler = ListSyncModelHandler(self)
        self.summary_status_handler = SummaryStatusHandler(self)
        self.topics_notes_handler = TopicsNotesHandler(self)
        self.ui_wizard_handler = UiWizardHandler(self)
        self._register_handlers()
        self._register_jobs()

    def _register_handlers(self) -> None:
        allow_filter = filters.ALL
        if self.settings.allowed_telegram_user_ids:
            allow_filter = filters.User(user_id=list(self.settings.allowed_telegram_user_ids))

        self.app.add_handler(MessageHandler(allow_filter, self._ingest_message), group=-1)
        self.app.add_handler(CommandHandler("help", self.help_command, filters=allow_filter))
        self.app.add_handler(CallbackQueryHandler(self.help_callback_handler, pattern=r"^help:"))
        self.app.add_handler(CallbackQueryHandler(self.draft_callback_handler, pattern=r"^draft:"))
        self.app.add_handler(CallbackQueryHandler(self.ui_callback_handler, pattern=r"^ui:"))
        self.app.add_handler(CommandHandler("add", self.add_command, filters=allow_filter))
        self.app.add_handler(CommandHandler("edit", self.edit_command, filters=allow_filter))
        self.app.add_handler(CommandHandler("done", self.done_command, filters=allow_filter))
        self.app.add_handler(CommandHandler("delete", self.delete_command, filters=allow_filter))
        self.app.add_handler(CommandHandler(["note", "notes"], self.notes_command, filters=allow_filter))
        self.app.add_handler(CommandHandler("list", self.list_command, filters=allow_filter))
        self.app.add_handler(CommandHandler("topics", self.topics_command, filters=allow_filter))
        self.app.add_handler(CommandHandler("topic", self.topic_command, filters=allow_filter))
        self.app.add_handler(CommandHandler("summary", self.summary_command, filters=allow_filter))
        self.app.add_handler(CommandHandler("sync", self.sync_command, filters=allow_filter))
        self.app.add_handler(CommandHandler("models", self.models_command, filters=allow_filter))
        self.app.add_handler(CommandHandler("model", self.model_command, filters=allow_filter))
        self.app.add_handler(CommandHandler("status", self.status_command, filters=allow_filter))
        self.app.add_handler(
            MessageHandler(
                (filters.PHOTO | filters.Document.ALL | filters.AUDIO | filters.VOICE | filters.VIDEO)
                & allow_filter
                & ~filters.COMMAND,
                self.attachment_message_handler,
            )
        )
        self.app.add_handler(MessageHandler(filters.TEXT & allow_filter & ~filters.COMMAND, self.normal_chat_handler))

    def _register_jobs(self) -> None:
        self.scheduler.add_job(self.process_due_reminders, "interval", seconds=30)
        self.scheduler.add_job(self.cleanup_archives, "cron", hour=1, minute=0)
        self.scheduler.add_job(self.cleanup_messages, "cron", hour=1, minute=15)
        self.scheduler.add_job(self.process_auto_summaries, "interval", minutes=1)
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
        if not update.message:
            return

        if context.args:
            topic = context.args[0].strip().lower()
            text = HELP_TOPICS.get(topic)
            if text:
                await update.message.reply_text(text)
                return
            await update.message.reply_text(
                HELP_TEXT + "\nUnknown topic. Try: reminders, notes, summaries, files, models, sync, examples"
            )
            return

        await update.message.reply_text(HELP_TEXT, reply_markup=self._help_keyboard())

    async def help_callback_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        query = update.callback_query
        if query is None:
            return

        if self.settings.allowed_telegram_user_ids and query.from_user:
            if int(query.from_user.id) not in set(self.settings.allowed_telegram_user_ids):
                await query.answer("Not authorized", show_alert=True)
                return

        data = (query.data or "").strip()
        if data == "help:cancel":
            await query.answer("Help closed")
            await query.edit_message_reply_markup(reply_markup=None)
            return

        if data == "help:back":
            await query.answer()
            try:
                await query.message.delete()
            except Exception:
                await query.edit_message_reply_markup(reply_markup=None)
            return

        if data.startswith("help:"):
            topic = data.split(":", 1)[1].strip().lower()
            text = HELP_TOPICS.get(topic)
            if not text:
                await query.answer("Unknown help topic", show_alert=True)
                return
            await query.answer()
            await query.message.reply_text(text, reply_markup=self._help_topic_keyboard())

    async def draft_callback_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        query = update.callback_query
        if query is None:
            return

        if self.settings.allowed_telegram_user_ids and query.from_user:
            if int(query.from_user.id) not in set(self.settings.allowed_telegram_user_ids):
                await query.answer("Not authorized", show_alert=True)
                return

        data = (query.data or "").strip().lower()
        action_map = {
            "draft:save": "confirm",
            "draft:topics": "confirm topics",
            "draft:show": "show",
            "draft:cancel": "cancel",
        }
        text_action = action_map.get(data)
        if not text_action:
            await query.answer("Unknown action", show_alert=True)
            return

        await query.answer()
        handled = await self.reminder_draft_manager.handle_followup(update, text_action)
        if not handled:
            await query.message.reply_text("No pending draft reminders right now.")
        elif data in {"draft:save", "draft:topics", "draft:cancel"}:
            try:
                await query.edit_message_reply_markup(reply_markup=None)
            except Exception:
                pass

    async def ui_callback_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await self.ui_wizard_handler.ui_callback_handler(update, context)

    def _help_keyboard(self) -> InlineKeyboardMarkup:
        rows = [
            [InlineKeyboardButton("Reminders", callback_data="help:reminders"), InlineKeyboardButton("Notes", callback_data="help:notes")],
            [InlineKeyboardButton("Summaries", callback_data="help:summaries")],
            [InlineKeyboardButton("Files", callback_data="help:files"), InlineKeyboardButton("Models", callback_data="help:models")],
            [InlineKeyboardButton("Sync", callback_data="help:sync"), InlineKeyboardButton("Examples", callback_data="help:examples")],
            [InlineKeyboardButton("Cancel", callback_data="help:cancel")],
        ]
        return InlineKeyboardMarkup(rows)

    def _help_topic_keyboard(self) -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup(
            [[InlineKeyboardButton("Back", callback_data="help:back"), InlineKeyboardButton("Cancel", callback_data="help:cancel")]]
        )

    def _notes_wizard_keyboard(self) -> InlineKeyboardMarkup:
        return self._ui_wizard_handler()._notes_wizard_keyboard()

    def _topics_wizard_keyboard(self) -> InlineKeyboardMarkup:
        return self._ui_wizard_handler()._topics_wizard_keyboard()

    def _delete_wizard_keyboard(self, confirm: bool = False) -> InlineKeyboardMarkup:
        return self._ui_wizard_handler()._delete_wizard_keyboard(confirm=confirm)

    def _edit_wizard_keyboard(self) -> InlineKeyboardMarkup:
        return self._ui_wizard_handler()._edit_wizard_keyboard()

    def _edit_topic_keyboard(self) -> InlineKeyboardMarkup:
        return self._ui_wizard_handler()._edit_topic_keyboard()

    def _ui_wizard_handler(self) -> UiWizardHandler:
        handler = getattr(self, "ui_wizard_handler", None)
        if handler is None:
            handler = UiWizardHandler(self)
            self.ui_wizard_handler = handler
        return handler

    async def add_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await self.add_edit_handler.add_command(update, context)

    async def _handle_pending_add_wizard(self, update: Update, text: str) -> bool:
        return await self.add_edit_handler.handle_pending_add_wizard(update, text)

    async def _handle_pending_edit_wizard(self, update: Update, text: str) -> bool:
        return await self._ui_wizard_handler()._handle_pending_edit_wizard(update, text)

    def _render_edit_wizard_menu(self, state: dict[str, str]) -> str:
        return self._ui_wizard_handler()._render_edit_wizard_menu(state)

    async def _handle_pending_model_wizard(self, update: Update, text: str) -> bool:
        return await self.list_sync_model_handler.handle_pending_model_wizard(update, text)

    async def _handle_pending_delete_wizard(self, update: Update, text: str) -> bool:
        return await self._ui_wizard_handler()._handle_pending_delete_wizard(update, text)

    async def _handle_pending_notes_wizard(self, update: Update, text: str) -> bool:
        return await self._ui_wizard_handler()._handle_pending_notes_wizard(update, text)

    async def _handle_pending_topics_wizard(self, update: Update, text: str) -> bool:
        return await self._ui_wizard_handler()._handle_pending_topics_wizard(update, text)

    async def _delete_reminder_by_id(self, update: Update, reminder_id: int) -> None:
        await self._ui_wizard_handler()._delete_reminder_by_id(update, reminder_id)

    def _collect_notes_candidates(self, chat_id: int) -> list[dict]:
        return self._ui_wizard_handler()._collect_notes_candidates(chat_id)

    async def _update_reminder_notes(self, chat_id: int, reminder_id: int, notes: str) -> bool:
        return await self._ui_wizard_handler()._update_reminder_notes(chat_id, reminder_id, notes)

    async def done_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not update.message:
            return
        self._clear_pending_flows(update.effective_chat.id)
        if not context.args:
            await update.message.reply_text(msg("usage_done"))
            return
        try:
            reminder_id = int(context.args[0])
        except ValueError:
            await update.message.reply_text(msg("error_id_number"))
            return

        ok = self.db.mark_done_and_archive_for_chat(reminder_id, update.effective_chat.id)
        if ok:
            await update.message.reply_text(msg("status_done_archived", id=reminder_id))
            await self._sync_calendar_delete(reminder_id)
        else:
            await update.message.reply_text(msg("error_done_not_found", id=reminder_id))

    async def edit_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await self.add_edit_handler.edit_command(update, context)

    async def delete_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not update.message:
            return
        self._clear_pending_flows(update.effective_chat.id, keep={"delete_wizard"})
        if not context.args:
            self.pending_delete_wizards[update.effective_chat.id] = {"step": "id"}
            await update.message.reply_text(
                "Delete wizard: enter reminder ID to delete, or `cancel`.",
                reply_markup=self._delete_wizard_keyboard(),
            )
            return
        try:
            reminder_id = int(context.args[0])
        except ValueError:
            await update.message.reply_text(msg("error_id_number"))
            return
        await self._delete_reminder_by_id(update, reminder_id)

    async def notes_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await self.topics_notes_handler.notes_command(update, context)

    def _is_notes_list_candidate(self, row: dict) -> bool:
        notes = str(row.get("notes") or "").strip()
        if not notes:
            return False

        source_kind = str(row.get("source_kind") or "").strip().lower()
        if source_kind == "group_summary":
            return len(notes) >= self.LONG_SUMMARY_NOTES_THRESHOLD

        created_at = str(row.get("created_at_utc") or "").strip()
        updated_at = str(row.get("updated_at_utc") or "").strip()
        # Treat notes edited/added later as manual notes worth listing.
        if created_at and updated_at and created_at != updated_at:
            return True

        return False

    async def list_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await self.list_sync_model_handler.list_command(update, context)

    async def _run_list_mode(self, update: Update, mode: str) -> None:
        await self.list_sync_model_handler.run_list_mode(update, mode)

    async def _reply_list_rows(self, update: Update, mode: str, rows) -> None:
        await self.list_sync_model_handler.reply_list_rows(update, mode, rows)

    async def summary_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await self.summary_status_handler.summary_command(update, context)

    async def topics_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await self.topics_notes_handler.topics_command(update, context)

    async def topic_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await self.topics_notes_handler.topic_command(update, context)

    def _split_topics(self, topic_text: str) -> list[str]:
        seen: set[str] = set()
        result: list[str] = []
        for part in topic_text.split(","):
            value = part.strip()
            if not value:
                continue
            key = value.lower()
            if key in seen:
                continue
            seen.add(key)
            result.append(value)
        return result

    def _clear_pending_flows(self, chat_id: int, keep: set[str] | None = None) -> None:
        self.flow_state_service.clear_pending_flows(chat_id, keep=keep)

    def _format_missing_topics_message(self, chat_id: int, missing_topics: list[str]) -> str:
        base = msg("error_topics_missing_create", topics=", ".join(missing_topics))
        suggestions: list[str] = []
        for missing in missing_topics:
            suggestions.extend(self.db.suggest_topics_for_chat(chat_id, missing, limit=3))
        deduped: list[str] = []
        seen: set[str] = set()
        for suggestion in suggestions:
            key = suggestion.lower()
            if key in seen:
                continue
            seen.add(key)
            deduped.append(suggestion)
        if not deduped:
            return base
        return base + "\n" + msg("status_topics_suggestions", topics=", ".join(deduped[:5]))

    async def sync_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await self.list_sync_model_handler.sync_command(update, context)

    async def _run_sync_mode(self, update: Update, mode: str) -> None:
        await self.list_sync_model_handler.run_sync_mode(update, mode)

    async def models_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await self.list_sync_model_handler.models_command(update, context)

    async def model_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await self.list_sync_model_handler.model_command(update, context)

    async def status_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await self.summary_status_handler.status_command(update, context)

    async def normal_chat_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await self.chat_pipeline_handler.normal_chat_handler(update, context)

    def _pending_workflow_handlers(self):
        return self.chat_pipeline_handler.pending_workflow_handlers()

    def _looks_like_inline_add_payload(self, raw: str) -> bool:
        text = (raw or "").lower()
        markers = (
            "at:",
            "p:",
            "priority:",
            "topic:",
            "t:",
            "every:",
            "link:",
            "notes:",
        )
        if any(marker in text for marker in markers):
            return True
        if re.search(r"(?:^|\s)#\w+", text):
            return True
        if re.search(r"(?:^|\s)!\s*(immediate|high|mid|low|i|h|m|l)\b", text):
            return True
        if re.search(r"(?:^|\s)@(daily|weekly|monthly)\b", text):
            return True
        return False

    async def attachment_message_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await self.chat_pipeline_handler.attachment_message_handler(update, context)

    def _load_vision_model_tags(self) -> set[str]:
        raw = self.db.get_app_setting("ollama_vision_tags") or ""
        tags = {part.strip() for part in raw.split(",") if part.strip()}
        return tags

    def _save_vision_model_tags(self) -> None:
        serialized = ",".join(sorted(self.vision_model_tags))
        self.db.set_app_setting("ollama_vision_tags", serialized)

    async def process_due_reminders(self) -> None:
        await self.job_runner.process_due_reminders()

    async def cleanup_archives(self) -> None:
        await self.job_runner.cleanup_archives()

    async def cleanup_messages(self) -> None:
        await self.job_runner.cleanup_messages()

    async def process_auto_summaries(self) -> None:
        await self.job_runner.process_auto_summaries()

    async def send_daily_digest(self) -> None:
        await self.job_runner.send_daily_digest()

    async def _build_group_summary(self, chat_id: int | None = None, save: bool = True) -> str:
        return await self.job_runner.build_group_summary(chat_id=chat_id, save=save)

    async def _summarize_group_rows(self, rows: list) -> str:
        return await self.job_runner.summarize_group_rows(rows)

    def _parse_add_payload(self, payload: str) -> dict[str, str]:
        text = payload.strip()
        links = re.findall(r"https?://\S+", text)
        first_link = links[0].rstrip(").,]") if links else ""

        topic_parts: list[str] = []
        topic_match = re.search(r"(?:topic|t)\s*:\s*(.+?)(?=\s+(?:link|p|priority|at|every)\s*:|$)", text, re.IGNORECASE)
        if topic_match:
            topic_parts.extend(self._split_topics(topic_match.group(1).strip()))
            text = (text[: topic_match.start()] + text[topic_match.end() :]).strip()

        hashtag_topics = re.findall(r"(?<!\w)#([A-Za-z0-9][A-Za-z0-9_-]{0,40})\b", text)
        if hashtag_topics:
            topic_parts.extend(hashtag_topics)
            text = re.sub(r"(?<!\w)#[A-Za-z0-9][A-Za-z0-9_-]{0,40}\b", " ", text)

        topic = ",".join(self._split_topics(",".join(topic_parts)))

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
            if self._is_no_due_text(dt_text):
                no_due_requested = True
            else:
                due_dt, due_confidence = self._parse_natural_datetime(dt_text)
            text = text[: at_match.start()].strip()
        else:
            no_due_match = re.search(r"\b(no\s+due(?:\s+date)?|no\s+deadline|someday|backlog)\b", text, re.IGNORECASE)
            if no_due_match:
                no_due_requested = True
                text = (text[: no_due_match.start()] + text[no_due_match.end() :]).strip()
            else:
                parsed_search = parse_datetime_text(text, self.settings.default_timezone)
                if parsed_search.dt is not None:
                    if self.settings.datetime_parse_debug:
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

    def _parse_edit_payload(self, payload: str) -> dict[str, object]:
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
                topic_values = self._split_topics(",".join(part.strip().lstrip("+") for part in parsed_topic.split(",")))
            elif parsed_topic.startswith("-"):
                topic_mode = "remove"
                topic_values = self._split_topics(",".join(part.strip().lstrip("-") for part in parsed_topic.split(",")))
            else:
                topic_mode = "replace"
                topic_values = self._split_topics(parsed_topic)

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
            if self._is_no_due_text(dt_text):
                due_at_utc = ""
            else:
                due_dt, _due_confidence = self._parse_natural_datetime(dt_text)
                if due_dt is None:
                    return {"error": msg("error_edit_invalid_due")}
                due_at_utc = due_dt.astimezone(timezone.utc).isoformat()

        if not any(value is not None for value in (title, notes, link, priority, due_at_utc, recurrence)) and not topic_mode:
            # Backward-compatible shorthand: /edit <id> <new title>
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

    def _parse_natural_datetime(self, dt_text: str) -> tuple[datetime | None, str]:
        parsed = parse_datetime_text(dt_text, self.settings.default_timezone)
        if parsed.dt is not None:
            if self.settings.datetime_parse_debug:
                LOGGER.info(
                    "parse_natural strategy=%s confidence=%s matched=%r input=%r",
                    parsed.strategy,
                    parsed.confidence,
                    parsed.matched_text,
                    dt_text,
                )
            return parsed.dt, parsed.confidence

        try:
            tz = ZoneInfo(self.settings.default_timezone)
        except Exception:
            tz = timezone.utc
        now_local = datetime.now(tz)

        parsed_llm = self._parse_datetime_with_llm(dt_text, now_local)
        if parsed_llm is not None:
            return parsed_llm, "medium"
        return None, "low"

    def _normalize_all_day_datetime(self, parsed_dt: datetime, raw_text: str) -> datetime:
        if self._has_explicit_time(raw_text):
            return parsed_dt
        try:
            tz = ZoneInfo(self.settings.default_timezone)
        except Exception:
            tz = timezone.utc
        local = parsed_dt.astimezone(tz) if parsed_dt.tzinfo else parsed_dt.replace(tzinfo=tz)
        return local.replace(hour=0, minute=0, second=0, microsecond=0)

    def _has_explicit_time(self, raw_text: str) -> bool:
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

    def _parse_datetime_with_llm(self, raw_text: str, now_local: datetime) -> datetime | None:
        prompt = datetime_fallback_prompt(
            user_text=raw_text,
            timezone_name=self.settings.default_timezone,
            now_iso=now_local.isoformat(),
        )
        raw = self.ollama.generate_text(prompt)
        parsed = self._parse_json_object(raw)
        if not parsed:
            return None

        confidence = str(parsed.get("confidence") or "").strip().lower()
        if confidence not in {"high", "medium"}:
            return None

        due_mode = str(parsed.get("due_mode") or "datetime").strip().lower()
        if due_mode not in {"datetime", "all_day", "none", "unclear"}:
            due_mode = "datetime"
        if due_mode in {"none", "unclear"}:
            return None

        due_text = str(parsed.get("due_text") or "").strip()
        if not due_text:
            return None

        due_dt = dateparser.parse(
            due_text,
            settings={
                "TIMEZONE": self.settings.default_timezone,
                "RETURN_AS_TIMEZONE_AWARE": True,
                "PREFER_DATES_FROM": "future",
                "RELATIVE_BASE": now_local,
            },
        )
        if due_dt is not None and due_mode == "all_day":
            due_dt = self._normalize_all_day_datetime(due_dt, due_text)
        return due_dt

    async def _handle_pending_add_confirmation(self, update: Update, text: str) -> bool:
        if not update.message or not update.effective_user:
            return False
        chat_id = update.effective_chat.id
        queue = self.pending_add_confirmations.get(chat_id)
        if not queue:
            return False
        pending = queue[0]

        lowered = text.strip().lower()
        if lowered in {"cancel", "skip"}:
            queue.pop(0)
            if not queue:
                self.pending_add_confirmations.pop(chat_id, None)
            await update.message.reply_text(msg("status_pending_add_cancelled"))
            return True

        if lowered in {"yes", "confirm", "ok", "okay"}:
            user_id = self.db.upsert_user(update.effective_user.id, update.effective_user.username, self.settings.default_timezone)
            reminder_id = self.db.create_reminder(
                user_id=user_id,
                source_message_id=None,
                source_kind="user_input",
                title=pending["title"],
                topic=pending.get("topic", ""),
                notes="",
                link=pending.get("link", ""),
                priority=pending["priority"],
                due_at_utc=pending["due_at_utc"],
                timezone_name=self.settings.default_timezone,
                chat_id_to_notify=chat_id,
                recurrence_rule=pending["recurrence"],
            )
            self.db.set_reminder_topics_for_chat(
                reminder_id,
                chat_id,
                self._split_topics(str(pending.get("topic") or "")),
            )
            queue.pop(0)
            if not queue:
                self.pending_add_confirmations.pop(chat_id, None)
            await update.message.reply_text(
                format_reminder_brief(reminder_id, pending["title"], pending["due_at_utc"], self.settings.default_timezone)
            )
            await self._sync_calendar_upsert(reminder_id)
            if queue:
                next_due = format_due_display(queue[0]["due_at_utc"], self.settings.default_timezone)
                await update.message.reply_text(
                    msg("status_due_guess", due_local=next_due, timezone=self.settings.default_timezone)
                )
            return True

        parsed_dt, confidence = self._parse_natural_datetime(text)
        if parsed_dt is None:
            await update.message.reply_text(msg("error_due_confirm_parse"))
            return True

        pending["due_at_utc"] = parsed_dt.astimezone(timezone.utc).isoformat()
        if confidence == "high":
            user_id = self.db.upsert_user(update.effective_user.id, update.effective_user.username, self.settings.default_timezone)
            reminder_id = self.db.create_reminder(
                user_id=user_id,
                source_message_id=None,
                source_kind="user_input",
                title=pending["title"],
                topic=pending.get("topic", ""),
                notes="",
                link=pending.get("link", ""),
                priority=pending["priority"],
                due_at_utc=pending["due_at_utc"],
                timezone_name=self.settings.default_timezone,
                chat_id_to_notify=chat_id,
                recurrence_rule=pending["recurrence"],
            )
            self.db.set_reminder_topics_for_chat(
                reminder_id,
                chat_id,
                self._split_topics(str(pending.get("topic") or "")),
            )
            queue.pop(0)
            if not queue:
                self.pending_add_confirmations.pop(chat_id, None)
            await update.message.reply_text(
                format_reminder_brief(reminder_id, pending["title"], pending["due_at_utc"], self.settings.default_timezone)
            )
            await self._sync_calendar_upsert(reminder_id)
            if queue:
                next_due = format_due_display(queue[0]["due_at_utc"], self.settings.default_timezone)
                await update.message.reply_text(
                    msg("status_due_guess", due_local=next_due, timezone=self.settings.default_timezone)
                )
            return True

        due_local = format_due_display(pending["due_at_utc"], self.settings.default_timezone)
        await update.message.reply_text(
            msg("status_due_recheck", due_local=due_local, timezone=self.settings.default_timezone)
        )
        return True

    def _parse_json_object(self, text: str) -> dict[str, str] | None:
        try:
            loaded = json.loads(text)
            if isinstance(loaded, dict):
                return {str(k): str(v) for k, v in loaded.items()}
        except Exception:
            pass

        match = re.search(r"\{[\s\S]*\}", text)
        if not match:
            return None
        try:
            loaded = json.loads(match.group(0))
        except Exception:
            return None
        if not isinstance(loaded, dict):
            return None
        return {str(k): str(v) for k, v in loaded.items()}

    def run_polling(self) -> None:
        logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s - %(message)s")
        logging.getLogger("httpx").setLevel(logging.WARNING)
        logging.getLogger("httpcore").setLevel(logging.WARNING)
        logging.getLogger("telegram").setLevel(logging.WARNING)
        self.userbot_ingest.start()
        self.scheduler.start()
        self.app.run_polling(drop_pending_updates=True)

    async def run_gpu_task(self, func, *args, **kwargs):
        async with self._gpu_task_lock:
            return await asyncio.to_thread(func, *args, **kwargs)

    async def _sync_to_google_calendar(self, update: Update) -> tuple[int, int, list[tuple[int, str]]]:
        push_total = 0
        push_ok = 0
        failures: list[tuple[int, str]] = []
        rows = self.db.list_reminders_for_chat(update.effective_chat.id)
        for row in rows:
            reminder_id = int(row["id"])
            push_total += 1
            ok = await asyncio.to_thread(self.calendar_sync.upsert_for_reminder_id, reminder_id)
            if ok:
                push_ok += 1
            else:
                reason = self.calendar_sync.get_last_error() or "unknown error"
                failures.append((reminder_id, reason))
        return push_total, push_ok, failures

    async def _sync_from_google_calendar(self, update: Update, allow_update_existing: bool) -> tuple[int, int]:
        events = await asyncio.to_thread(self.calendar_sync.list_upcoming_events, 180)
        created = 0
        updated = 0
        skipped_existing = 0
        skipped_missing_start = 0
        user_id = self.db.upsert_user(update.effective_user.id, update.effective_user.username, self.settings.default_timezone)

        for event in events:
            event_id = str(event.get("id") or "").strip()
            if not event_id:
                continue
            if self.db.is_calendar_event_tombstoned(event_id, provider="google", ttl_days=30):
                continue
            due_at_utc = self._calendar_event_to_due_utc(event)
            if not due_at_utc:
                skipped_missing_start += 1
                continue

            title = str(event.get("summary") or "Google Calendar event").strip()
            raw_notes = str(event.get("description") or "").strip()
            link = str(event.get("htmlLink") or "").strip() or self._extract_first_url(raw_notes)
            notes = self._clean_calendar_import_notes(raw_notes)

            mapped_reminder_id = self.db.get_reminder_id_by_calendar_event_id(event_id, provider="google")
            if mapped_reminder_id:
                row = self.db.get_reminder_by_id(mapped_reminder_id)
                if row is None:
                    continue
                reminder = dict(row)
                if reminder.get("status") != "open":
                    continue

                if not allow_update_existing:
                    skipped_existing += 1
                    continue

                changed = (
                    str(reminder.get("title") or "") != title
                    or str(reminder.get("notes") or "") != notes
                    or str(reminder.get("link") or "") != link
                    or str(reminder.get("due_at_utc") or "") != due_at_utc
                )
                if changed:
                    self.db.update_reminder_fields(
                        reminder_id=mapped_reminder_id,
                        title=title,
                        topic=str(reminder.get("topic") or ""),
                        notes=notes,
                        link=link,
                        priority=str(reminder.get("priority") or "mid"),
                        due_at_utc=due_at_utc,
                        recurrence_rule=reminder.get("recurrence_rule"),
                    )
                    updated += 1
                continue

            reminder_id = self.db.create_reminder(
                user_id=user_id,
                source_message_id=None,
                source_kind="google_calendar_import",
                title=title,
                topic="",
                notes=notes,
                link=link,
                priority="mid",
                due_at_utc=due_at_utc,
                timezone_name=self.settings.default_timezone,
                chat_id_to_notify=update.effective_chat.id,
                recurrence_rule=None,
            )
            self.db.upsert_calendar_event_id(reminder_id, event_id, provider="google")
            created += 1

        LOGGER.info(
            "Calendar pull processed events=%s created=%s updated=%s skipped_existing=%s skipped_no_start=%s",
            len(events),
            created,
            updated,
            skipped_existing,
            skipped_missing_start,
        )
        return created, updated

    def _calendar_event_to_due_utc(self, event: dict) -> str:
        start = event.get("start") or {}
        if not isinstance(start, dict):
            return ""
        date_time_text = str(start.get("dateTime") or "").strip()
        if date_time_text:
            normalized = date_time_text.replace("Z", "+00:00")
            try:
                dt = datetime.fromisoformat(normalized)
            except ValueError:
                return ""
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc).isoformat()

        date_only_text = str(start.get("date") or "").strip()
        if not date_only_text:
            return ""
        try:
            date_only = datetime.strptime(date_only_text, "%Y-%m-%d")
        except ValueError:
            return ""
        try:
            tz = ZoneInfo(self.settings.default_timezone)
        except Exception:
            tz = timezone.utc
        local_dt = date_only.replace(tzinfo=tz, hour=0, minute=0, second=0, microsecond=0)
        return local_dt.astimezone(timezone.utc).isoformat()

    def _extract_first_url(self, text: str) -> str:
        match = re.search(r"https?://\S+", text or "")
        if not match:
            return ""
        return match.group(0).rstrip(").,]")

    def _clean_calendar_import_notes(self, notes: str) -> str:
        lines = [line.rstrip() for line in (notes or "").splitlines()]
        cleaned: list[str] = []
        for line in lines:
            stripped = line.strip()
            lowered = stripped.lower()
            if not stripped:
                if cleaned and cleaned[-1] != "":
                    cleaned.append("")
                continue
            if lowered.startswith("reminder id:"):
                continue
            if lowered.startswith("priority:"):
                continue
            if lowered.startswith("link:"):
                continue
            if lowered.startswith("topic:"):
                continue
            cleaned.append(stripped)

        while cleaned and cleaned[-1] == "":
            cleaned.pop()
        return "\n".join(cleaned).strip()

    def _is_no_due_text(self, text: str) -> bool:
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

    def _list_mode_in_local_timezone(self, mode: str) -> list:
        try:
            tz = ZoneInfo(self.settings.default_timezone)
        except Exception:
            tz = timezone.utc
        now_local = datetime.now(tz)
        start_today_local = now_local.replace(hour=0, minute=0, second=0, microsecond=0)

        if mode == "today":
            start_utc = start_today_local.astimezone(timezone.utc).isoformat()
            end_utc = (start_today_local + timedelta(days=1)).astimezone(timezone.utc).isoformat()
            return self.db.list_reminders_between(start_utc, end_utc)

        if mode == "tomorrow":
            start_local = start_today_local + timedelta(days=1)
            start_utc = start_local.astimezone(timezone.utc).isoformat()
            end_utc = (start_local + timedelta(days=1)).astimezone(timezone.utc).isoformat()
            return self.db.list_reminders_between(start_utc, end_utc)

        if mode == "overdue":
            cutoff_utc = now_local.astimezone(timezone.utc).isoformat()
            return self.db.list_reminders_before(cutoff_utc)

        return []

    async def _sync_calendar_upsert(self, reminder_id: int) -> None:
        if not self.calendar_sync.is_enabled():
            return
        await asyncio.to_thread(self.calendar_sync.upsert_for_reminder_id, reminder_id)

    async def _sync_calendar_delete(self, reminder_id: int) -> None:
        if not self.calendar_sync.is_enabled():
            return
        await asyncio.to_thread(self.calendar_sync.delete_for_reminder_id, reminder_id)
