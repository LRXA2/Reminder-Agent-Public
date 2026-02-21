from __future__ import annotations

from telegram import Update


def get_reply_target(update: Update):
    return update.message or (update.callback_query.message if update.callback_query else None)
