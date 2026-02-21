from __future__ import annotations

__all__ = ["ReminderBot"]


def __getattr__(name: str):
    if name == "ReminderBot":
        from src.app.reminder_bot import ReminderBot

        return ReminderBot
    raise AttributeError(name)
