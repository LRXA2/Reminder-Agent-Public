from __future__ import annotations

from typing import TYPE_CHECKING

from telegram import Update
from telegram.ext import ContextTypes

from src.app.messages import msg

if TYPE_CHECKING:
    from src.app.bot_orchestrator import ReminderBot


class SummaryStatusHandler:
    def __init__(self, bot: "ReminderBot") -> None:
        self.bot = bot

    async def summary_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not update.message:
            return
        self.bot.flow_state_service.clear_pending_flows(update.effective_chat.id)

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
            summary = await self.bot.job_runner.build_group_summary(chat_id=target_chat_id)
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
        self.bot.flow_state_service.clear_pending_flows(update.effective_chat.id)

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

    async def gmail_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not update.message:
            return
        self.bot.flow_state_service.clear_pending_flows(update.effective_chat.id)

        args = [str(part).strip() for part in (context.args or []) if str(part).strip()]
        subcommand = args[0].lower() if args else "status"

        if subcommand == "accounts":
            account_ids = self.bot.gmail_ingest_handler.account_ids()
            if not account_ids:
                await update.message.reply_text("No Gmail accounts configured. Set GMAIL_ACCOUNTS_JSON first.")
                return
            await update.message.reply_text("Configured Gmail accounts:\n- " + "\n- ".join(account_ids))
            return

        if subcommand == "sync":
            await update.message.reply_text("Running Gmail sync now...")
            stats = await self.bot.gmail_ingest_handler.poll_all_accounts(force_batch_flush=True)
            await update.message.reply_text(
                "Gmail sync complete. "
                f"accounts={stats.get('accounts', 0)}, "
                f"processed={stats.get('processed', 0)}, "
                f"important={stats.get('important', 0)}, "
                f"notified={stats.get('notified', 0)}, "
                f"errors={stats.get('errors', 0)}"
            )
            return

        enabled = self.bot.settings.gmail_enabled
        account_count = self.bot.gmail_ingest_handler.account_count()
        lines = [
            f"Gmail ingest: {'enabled' if enabled else 'disabled'}",
            f"Configured accounts: {account_count}",
            f"Poll interval: {self.bot.settings.gmail_poll_interval_minutes} minute(s)",
            f"Global query: {self.bot.settings.gmail_global_query or '(none)'}",
            f"LLM importance: {'on' if self.bot.settings.gmail_use_llm_importance else 'off'}",
            f"Require rule match: {'on' if self.bot.settings.gmail_require_rule_match else 'off'}",
            f"Summary generation: {'on' if self.bot.settings.gmail_summary_enabled else 'off'}",
            f"Delivery mode: {self.bot.settings.gmail_delivery_mode}",
            f"Batch interval: {self.bot.settings.gmail_batch_interval_minutes} minute(s)",
            f"Urgent threshold: {self.bot.settings.gmail_urgent_score_threshold:.2f}",
            f"Thread cooldown: {self.bot.settings.gmail_thread_cooldown_minutes} minute(s)",
            "Usage: /gmail status | /gmail accounts | /gmail sync",
        ]
        states = self.bot.db.list_gmail_account_states()
        if states:
            lines.append("Recent account state:")
            for row in states[:10]:
                account_id = str(row["account_id"] or "")
                checked = str(row["last_checked_at_utc"] or "")
                error = str(row["last_error"] or "").strip()
                suffix = f" error={error}" if error else ""
                lines.append(f"- {account_id}: {checked}{suffix}")
        await update.message.reply_text("\n".join(lines))
