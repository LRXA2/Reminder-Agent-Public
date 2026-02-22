from __future__ import annotations

from typing import TYPE_CHECKING

from telegram import Update
from telegram.ext import ContextTypes

from src.app.handlers.commands.add_edit.commands_flow import AddEditCommandsFlow
from src.app.handlers.commands.add_edit.confirmation_workflow import AddConfirmationWorkflow
from src.app.handlers.commands.add_edit.parsing import AddEditPayloadParser
from src.app.handlers.commands.add_edit.wizard_workflow import AddWizardWorkflow

if TYPE_CHECKING:
    from src.app.bot_orchestrator import ReminderBot


class AddEditHandler:
    def __init__(self, bot: "ReminderBot") -> None:
        self.bot = bot
        self.payload_parser = AddEditPayloadParser(bot)
        self.command_flow = AddEditCommandsFlow(bot, self.payload_parser)
        self.confirmation_workflow = AddConfirmationWorkflow(bot)
        self.wizard_workflow = AddWizardWorkflow(bot)

    async def add_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await self.command_flow.add_command(update, context)

    async def edit_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await self.command_flow.edit_command(update, context)

    def parse_add_payload(self, payload: str) -> dict[str, str]:
        return self.payload_parser.parse_add_payload(payload)

    def parse_edit_payload(self, payload: str) -> dict[str, object]:
        return self.payload_parser.parse_edit_payload(payload)

    async def handle_pending_add_confirmation(self, update: Update, text: str) -> bool:
        return await self.confirmation_workflow.handle_pending_add_confirmation(update, text)

    async def handle_pending_add_wizard(self, update: Update, text: str) -> bool:
        return await self.wizard_workflow.handle_pending_add_wizard(update, text)
