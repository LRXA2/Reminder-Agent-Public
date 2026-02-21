from __future__ import annotations

__all__ = ["ReminderDraftManager"]


def __getattr__(name: str):
    if name == "ReminderDraftManager":
        from .manager import ReminderDraftManager

        return ReminderDraftManager
    raise AttributeError(name)
