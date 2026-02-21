from __future__ import annotations

from typing import TYPE_CHECKING

from telegram import Update
from telegram.ext import ContextTypes

from src.app.messages import msg

if TYPE_CHECKING:
    from src.app.reminder_bot import ReminderBot


class SummaryStatusHandler:
    def __init__(self, bot: "ReminderBot") -> None:
        self.bot = bot

    async def summary_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not update.message:
            return
        self.bot._clear_pending_flows(update.effective_chat.id)

        target_chat_id = self.bot.settings.monitored_group_chat_id
        if context.args:
            try:
                target_chat_id = int(context.args[0].strip())
            except ValueError:
                await update.message.reply_text(msg("usage_summary"))
                return
        elif not target_chat_id and self.bot.settings.userbot_ingest_chat_ids:
            target_chat_id = int(self.bot.settings.userbot_ingest_chat_ids[0])

        if not target_chat_id:
            await update.message.reply_text(msg("error_summary_target"))
            return

        await update.message.reply_text(msg("status_summary_start"))
        try:
            summary = await self.bot._build_group_summary(chat_id=target_chat_id)
            await update.message.reply_text(summary)
            if summary.startswith("No recent messages found for chat"):
                return
            await update.message.reply_text(msg("status_summary_done"))
            await self.bot.reminder_draft_manager.propose_from_text(
                update=update,
                source_kind="group_summary",
                content=summary,
                user_instruction=f"/summary {target_chat_id}",
            )
        except Exception as exc:
            await update.message.reply_text(msg("error_summary_run", error=exc))

    async def status_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not update.message:
            return
        self.bot._clear_pending_flows(update.effective_chat.id)

        ollama_ready = self.bot.ollama.ensure_server(
            autostart=False,
            timeout_seconds=2,
            use_highest_vram_gpu=False,
        )
        text_model = self.bot.ollama.get_text_model() or "(none)"
        vision_model = self.bot.ollama.get_vision_model() or "(none)"
        gpu = self.bot.ollama.detect_nvidia_gpu()
        ps_output = self.bot.ollama.ollama_ps()

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
