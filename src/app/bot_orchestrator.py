from __future__ import annotations

import asyncio
import logging

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
from src.app.handlers.commands.add_edit.handler import AddEditHandler
from src.app.handlers.commands.completion_delete_handler import CompletionDeleteHandler
from src.app.handlers.commands.list_sync_models_handler import ListSyncModelHandler
from src.app.handlers.commands.summary_status_handler import SummaryStatusHandler
from src.app.handlers.commands.topics_notes_commands import TopicsNotesHandler
from src.app.handlers.runtime.message_pipeline import ChatPipelineHandler
from src.app.handlers.runtime.flow_state_service import FlowStateService
from src.app.handlers.runtime.message_ingest_handler import MessageIngestHandler
from src.app.handlers.services.calendar_sync_handler import CalendarSyncHandler
from src.app.handlers.services.datetime_resolution_handler import DateTimeResolutionHandler
from src.app.handlers.services.scheduler_jobs import JobRunner
from src.app.handlers.services.reminder_rules import ReminderLogicHandler
from src.app.handlers.services.vision_model_tags import VisionModelTagHandler
from src.app.handlers.reminder_draft import ReminderDraftManager
from src.app.handlers.text_input import TextInputHandler
from src.app.handlers.wizards import UiWizardHandler
from src.app.messages import HELP_TEXT, HELP_TOPICS
from src.clients.ollama_client import OllamaClient
from src.clients.stt_client import SttClient
from src.core.config import Settings
from src.integrations.google_calendar_service import GoogleCalendarSyncService
from src.storage.database import Database
from src.userbot import UserbotIngestService


LOGGER = logging.getLogger(__name__)


class ReminderBot:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.db = Database(settings.db_path)
        self.vision_model_tag_handler = VisionModelTagHandler(self)
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

        self.vision_model_tags = self.vision_model_tag_handler.load_tags()
        current_vision = self.ollama.get_vision_model()
        if current_vision:
            self.vision_model_tags.add(current_vision)
            self.vision_model_tag_handler.save_tags()
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
        self.calendar_sync_handler = CalendarSyncHandler(self)
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
            on_reminder_created=self.calendar_sync_handler.sync_calendar_upsert,
        )
        self.text_input_handler = TextInputHandler(
            self.db,
            self.ollama,
            self.settings,
            self.reminder_draft_manager,
            run_gpu_task=self.run_gpu_task,
            on_reminder_created=self.calendar_sync_handler.sync_calendar_upsert,
            on_reminder_updated=self.calendar_sync_handler.sync_calendar_upsert,
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
        self.datetime_resolution_handler = DateTimeResolutionHandler(self)
        self.flow_state_service = FlowStateService(self)
        self.job_runner = JobRunner(self)
        self.list_sync_model_handler = ListSyncModelHandler(self)
        self.message_ingest_handler = MessageIngestHandler(self)
        self.reminder_logic_handler = ReminderLogicHandler(self)
        self.summary_status_handler = SummaryStatusHandler(self)
        self.completion_delete_handler = CompletionDeleteHandler(self)
        self.topics_notes_handler = TopicsNotesHandler(self)
        self.ui_wizard_handler = UiWizardHandler(self)
        self._register_handlers()
        self._register_jobs()

    def _register_handlers(self) -> None:
        allow_filter = filters.ALL
        if self.settings.allowed_telegram_user_ids:
            allow_filter = filters.User(user_id=list(self.settings.allowed_telegram_user_ids))

        self.app.add_handler(MessageHandler(allow_filter, self.message_ingest_handler.ingest_message), group=-1)
        self.app.add_handler(CommandHandler("help", self.help_command, filters=allow_filter))
        self.app.add_handler(CallbackQueryHandler(self.help_callback_handler, pattern=r"^help:"))
        self.app.add_handler(CallbackQueryHandler(self.draft_callback_handler, pattern=r"^draft:"))
        self.app.add_handler(CallbackQueryHandler(self.ui_wizard_handler.ui_callback_handler, pattern=r"^ui:"))
        self.app.add_handler(CommandHandler("add", self.add_edit_handler.add_command, filters=allow_filter))
        self.app.add_handler(CommandHandler("edit", self.add_edit_handler.edit_command, filters=allow_filter))
        self.app.add_handler(CommandHandler("done", self.completion_delete_handler.done_command, filters=allow_filter))
        self.app.add_handler(CommandHandler("delete", self.completion_delete_handler.delete_command, filters=allow_filter))
        self.app.add_handler(CommandHandler(["note", "notes"], self.topics_notes_handler.notes_command, filters=allow_filter))
        self.app.add_handler(CommandHandler("list", self.list_sync_model_handler.list_command, filters=allow_filter))
        self.app.add_handler(CommandHandler("topics", self.topics_notes_handler.topics_command, filters=allow_filter))
        self.app.add_handler(CommandHandler("topic", self.topics_notes_handler.topic_command, filters=allow_filter))
        self.app.add_handler(CommandHandler("summary", self.summary_status_handler.summary_command, filters=allow_filter))
        self.app.add_handler(CommandHandler("sync", self.list_sync_model_handler.sync_command, filters=allow_filter))
        self.app.add_handler(CommandHandler("models", self.list_sync_model_handler.models_command, filters=allow_filter))
        self.app.add_handler(CommandHandler("model", self.list_sync_model_handler.model_command, filters=allow_filter))
        self.app.add_handler(CommandHandler("status", self.summary_status_handler.status_command, filters=allow_filter))
        self.app.add_handler(
            MessageHandler(
                (filters.PHOTO | filters.Document.ALL | filters.AUDIO | filters.VOICE | filters.VIDEO)
                & allow_filter
                & ~filters.COMMAND,
                self.chat_pipeline_handler.attachment_message_handler,
            )
        )
        self.app.add_handler(MessageHandler(filters.TEXT & allow_filter & ~filters.COMMAND, self.chat_pipeline_handler.normal_chat_handler))

    def _register_jobs(self) -> None:
        self.scheduler.add_job(self.job_runner.process_due_reminders, "interval", seconds=30)
        self.scheduler.add_job(self.job_runner.cleanup_archives, "cron", hour=1, minute=0)
        self.scheduler.add_job(self.job_runner.cleanup_messages, "cron", hour=1, minute=15)
        self.scheduler.add_job(self.job_runner.process_auto_summaries, "interval", minutes=1)
        digest_times = self.settings.digest_times_local or (
            (self.settings.digest_hour_local, self.settings.digest_minute_local),
        )
        seen: set[tuple[int, int]] = set()
        for hour, minute in digest_times:
            if (hour, minute) in seen:
                continue
            seen.add((hour, minute))
            self.scheduler.add_job(
                self.job_runner.send_daily_digest,
                "cron",
                hour=hour,
                minute=minute,
            )

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
