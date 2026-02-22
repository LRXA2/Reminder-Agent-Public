from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from src.app.handlers.reminder_formatting import format_reminder_list_item
from src.app.messages import msg

if TYPE_CHECKING:
    from src.app.bot_orchestrator import ReminderBot


LOGGER = logging.getLogger(__name__)


class ListSyncModelHandler:
    def __init__(self, bot: "ReminderBot") -> None:
        self.bot = bot

    async def handle_pending_model_wizard(self, update: Update, text: str) -> bool:
        if not update.message:
            return False
        chat_id = update.effective_chat.id
        state = self.bot.pending_model_wizards.get(chat_id)
        if not state:
            return False

        raw = (text or "").strip()
        lowered = raw.lower()
        if lowered in {"cancel", "stop"}:
            self.bot.pending_model_wizards.pop(chat_id, None)
            await update.message.reply_text("Model wizard cancelled.")
            return True

        step = state.get("step", "role")
        if step == "role":
            if lowered not in {"text", "vision"}:
                await update.message.reply_text("Choose `text` or `vision` (or `cancel`).")
                return True
            state["role"] = lowered
            state["step"] = "name"
            models = self.bot.ollama.list_models()
            await update.message.reply_text(
                f"Step 2/2 - Enter model name for {lowered}.\nInstalled:\n- " + "\n- ".join(models)
            )
            return True

        if step == "name":
            models = self.bot.ollama.list_models()
            chosen = raw
            if chosen not in models:
                await update.message.reply_text(msg("error_model_not_installed", model=chosen))
                return True
            role = str(state.get("role") or "text")
            if role == "vision":
                self.bot.ollama.set_vision_model(chosen)
                self.bot.db.set_app_setting("ollama_vision_model", chosen)
                self.bot.vision_model_tags.add(chosen)
                self.bot.vision_model_tag_handler.save_tags()
                await update.message.reply_text(msg("status_model_set_vision", model=chosen))
            else:
                self.bot.ollama.set_text_model(chosen)
                self.bot.db.set_app_setting("ollama_text_model", chosen)
                self.bot.db.set_app_setting("ollama_model", chosen)
                await update.message.reply_text(msg("status_model_set_text", model=chosen))
            self.bot.pending_model_wizards.pop(chat_id, None)
            return True

        self.bot.pending_model_wizards.pop(chat_id, None)
        return False

    async def list_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not update.message:
            return
        self.bot.flow_state_service.clear_pending_flows(update.effective_chat.id)

        if not context.args:
            await update.message.reply_text(
                "Choose list filter:",
                reply_markup=InlineKeyboardMarkup(
                    [
                        [InlineKeyboardButton("All", callback_data="ui:list:all"), InlineKeyboardButton("Archived", callback_data="ui:list:archived")],
                        [InlineKeyboardButton("Today", callback_data="ui:list:today"), InlineKeyboardButton("Tomorrow", callback_data="ui:list:tomorrow")],
                        [InlineKeyboardButton("Overdue", callback_data="ui:list:overdue")],
                    ]
                ),
            )
            return

        mode = context.args[0].lower()
        rows = []
        try:
            if re.fullmatch(r"-?\d+", mode):
                rows = self.bot.db.list_reminders_for_chat(int(mode))
            elif mode == "all":
                rows = self.bot.db.list_reminders("all")
            elif mode == "priority" and len(context.args) >= 2:
                rows = self.bot.db.list_reminders("priority", context.args[1].lower())
            elif mode == "topic" and len(context.args) >= 2:
                rows = self.bot.db.list_reminders("topic", " ".join(context.args[1:]).strip())
            elif mode == "archived":
                if len(context.args) >= 3 and context.args[1].lower() == "topic":
                    rows = self.bot.db.list_archived_reminders_for_chat(
                        update.effective_chat.id,
                        " ".join(context.args[2:]).strip(),
                    )
                elif len(context.args) == 1:
                    rows = self.bot.db.list_archived_reminders_for_chat(update.effective_chat.id)
                else:
                    await update.message.reply_text(msg("usage_list"))
                    return
            elif mode == "due" and len(context.args) >= 2:
                value = context.args[1].lower().strip()
                if not value.endswith("d"):
                    await update.message.reply_text(msg("usage_list_due"))
                    return
                rows = self.bot.db.list_reminders("due_days", value[:-1])
            elif mode in {"today", "tomorrow", "overdue"}:
                rows = self.list_mode_in_local_timezone(mode)
            else:
                await update.message.reply_text(msg("error_list_unknown"))
                return
        except ValueError:
            await update.message.reply_text(msg("error_list_invalid"))
            return

        await self.reply_list_rows(update, mode, rows)

    async def run_list_mode(self, update: Update, mode: str) -> None:
        mode = (mode or "").strip().lower()
        target = update.message or (update.callback_query.message if update.callback_query else None)
        if target is None:
            return

        if mode == "all":
            rows = self.bot.db.list_reminders("all")
        elif mode == "archived":
            rows = self.bot.db.list_archived_reminders_for_chat(update.effective_chat.id)
        elif mode in {"today", "tomorrow", "overdue"}:
            rows = self.list_mode_in_local_timezone(mode)
        else:
            await target.reply_text(msg("usage_list"))
            return
        await self.reply_list_rows(update, mode, rows)

    def list_mode_in_local_timezone(self, mode: str) -> list:
        try:
            tz = ZoneInfo(self.bot.settings.default_timezone)
        except Exception:
            tz = timezone.utc
        now_local = datetime.now(tz)
        start_today_local = now_local.replace(hour=0, minute=0, second=0, microsecond=0)

        if mode == "today":
            start_utc = start_today_local.astimezone(timezone.utc).isoformat()
            end_utc = (start_today_local + timedelta(days=1)).astimezone(timezone.utc).isoformat()
            return self.bot.db.list_reminders_between(start_utc, end_utc)

        if mode == "tomorrow":
            start_local = start_today_local + timedelta(days=1)
            start_utc = start_local.astimezone(timezone.utc).isoformat()
            end_utc = (start_local + timedelta(days=1)).astimezone(timezone.utc).isoformat()
            return self.bot.db.list_reminders_between(start_utc, end_utc)

        if mode == "overdue":
            cutoff_utc = now_local.astimezone(timezone.utc).isoformat()
            return self.bot.db.list_reminders_before(cutoff_utc)

        return []

    async def reply_list_rows(self, update: Update, mode: str, rows) -> None:
        target = update.message or (update.callback_query.message if update.callback_query else None)
        if target is None:
            return
        if not rows:
            await target.reply_text(msg("error_list_archived_empty" if mode == "archived" else "error_list_empty"))
            return

        lines = ["Archived reminders:" if mode == "archived" else "Open reminders:"]
        for idx, row in enumerate(rows[:30], start=1):
            lines.append(format_reminder_list_item(idx, dict(row), self.bot.settings.default_timezone))
        if len(rows) > 30:
            lines.append(f"...and {len(rows) - 30} more. Use /list due 14d, /list priority high, or /list topic <name> to narrow.")
        await target.reply_text("\n\n".join(lines))

    async def sync_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not update.message or not update.effective_user:
            return
        self.bot.flow_state_service.clear_pending_flows(update.effective_chat.id)
        if not self.bot.calendar_sync.is_enabled():
            await update.message.reply_text(msg("error_sync_disabled"))
            return

        if not context.args:
            await update.message.reply_text(
                "Choose sync mode:",
                reply_markup=InlineKeyboardMarkup(
                    [
                        [InlineKeyboardButton("Both", callback_data="ui:sync:both")],
                        [InlineKeyboardButton("Import", callback_data="ui:sync:import"), InlineKeyboardButton("Export", callback_data="ui:sync:export")],
                    ]
                ),
            )
            return

        sync_arg = context.args[0].strip().lower()
        mode_aliases = {
            "both": "both",
            "import": "import",
            "export": "export",
        }
        mode = mode_aliases.get(sync_arg)
        if mode is None:
            await update.message.reply_text(msg("usage_sync"))
            return

        await self.run_sync_mode(update, mode)

    async def run_sync_mode(self, update: Update, mode: str) -> None:
        target = update.message or (update.callback_query.message if update.callback_query else None)
        if target is None:
            return

        await target.reply_text(msg("status_sync_start", mode=mode))

        failed_pushes: list[tuple[int, str]] = []

        if mode == "import":
            pull_created, pull_updated = await self.bot.calendar_sync_handler.sync_from_google_calendar(update, allow_update_existing=True)
            push_total, push_ok = 0, 0
        elif mode == "export":
            push_total, push_ok, failed_pushes = await self.bot.calendar_sync_handler.sync_to_google_calendar(update)
            pull_created, pull_updated = 0, 0
        else:
            push_total, push_ok, failed_pushes = await self.bot.calendar_sync_handler.sync_to_google_calendar(update)
            pull_created, pull_updated = await self.bot.calendar_sync_handler.sync_from_google_calendar(update, allow_update_existing=False)

        LOGGER.info(
            "Manual /sync done mode=%s push_ok=%s/%s pull_created=%s pull_updated=%s",
            mode,
            push_ok,
            push_total,
            pull_created,
            pull_updated,
        )
        await target.reply_text(
            msg(
                "status_sync_done",
                mode=mode,
                push_ok=push_ok,
                push_total=push_total,
                pull_created=pull_created,
                pull_updated=pull_updated,
            )
        )
        if failed_pushes:
            details = "; ".join(f"#{reminder_id} ({reason})" for reminder_id, reason in failed_pushes[:3])
            if len(failed_pushes) > 3:
                details += f"; +{len(failed_pushes) - 3} more"
            await target.reply_text(msg("status_sync_failed_details", details=details))

    async def models_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not update.message:
            return
        self.bot.flow_state_service.clear_pending_flows(update.effective_chat.id)
        models = self.bot.ollama.list_models()
        if not models:
            await update.message.reply_text(msg("error_models_empty"))
            return

        active_text = self.bot.ollama.get_text_model()
        active_vision = self.bot.ollama.get_vision_model()
        lines = ["Installed Ollama models:"]
        for model in models:
            markers: list[str] = []
            if model == active_text:
                markers.append("text")
            if model == active_vision:
                markers.append("vision-active")
            if model in self.bot.vision_model_tags:
                markers.append("vision")
            marker_text = f" ({', '.join(markers)})" if markers else ""
            lines.append(f"- {model}{marker_text}")
        await update.message.reply_text("\n".join(lines))

    async def model_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not update.message:
            return
        self.bot.flow_state_service.clear_pending_flows(update.effective_chat.id, keep={"model_wizard"})

        if not context.args:
            models = self.bot.ollama.list_models()
            self.bot.pending_model_wizards[update.effective_chat.id] = {"step": "role"}
            await update.message.reply_text(
                "Model wizard started. Step 1/2 - Choose role: `text` or `vision` (or `cancel`).\n"
                + ("Installed:\n- " + "\n- ".join(models) if models else "No models installed.")
            )
            return

        models = self.bot.ollama.list_models()
        first = context.args[0].lower()

        if first in {"tag", "untag"}:
            if len(context.args) < 3 or context.args[-1].lower() != "vision":
                await update.message.reply_text(msg("usage_model_tag"))
                return
            target = " ".join(context.args[1:-1]).strip()
            if target not in models:
                await update.message.reply_text(msg("error_model_not_installed", model=target))
                return
            if first == "tag":
                self.bot.vision_model_tags.add(target)
                self.bot.vision_model_tag_handler.save_tags()
                await update.message.reply_text(msg("status_model_tagged", model=target))
            else:
                self.bot.vision_model_tags.discard(target)
                self.bot.vision_model_tag_handler.save_tags()
                await update.message.reply_text(msg("status_model_untagged", model=target))
            return

        target_role = "text"
        if first in {"text", "vision"}:
            target_role = first
            chosen = " ".join(context.args[1:]).strip()
            if not chosen:
                await update.message.reply_text(msg("usage_model_role", role=target_role))
                return
        else:
            chosen = " ".join(context.args).strip()

        if chosen not in models:
            await update.message.reply_text(msg("error_model_not_installed", model=chosen))
            return

        if target_role == "vision":
            self.bot.ollama.set_vision_model(chosen)
            self.bot.db.set_app_setting("ollama_vision_model", chosen)
            self.bot.vision_model_tags.add(chosen)
            self.bot.vision_model_tag_handler.save_tags()
            await update.message.reply_text(msg("status_model_set_vision", model=chosen))
            return

        self.bot.ollama.set_text_model(chosen)
        self.bot.db.set_app_setting("ollama_text_model", chosen)
        self.bot.db.set_app_setting("ollama_model", chosen)
        await update.message.reply_text(msg("status_model_set_text", model=chosen))
