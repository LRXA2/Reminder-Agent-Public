from __future__ import annotations

from telegram import Update


class OperationStatus:
    @staticmethod
    async def started(update: Update, message: str) -> None:
        if update.message:
            await update.message.reply_text(message)

    @staticmethod
    async def done(update: Update, message: str) -> None:
        if update.message:
            await update.message.reply_text(message)

    @staticmethod
    async def error(update: Update, message: str) -> None:
        if update.message:
            await update.message.reply_text(message)
