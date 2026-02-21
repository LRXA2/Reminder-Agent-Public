from __future__ import annotations

__all__ = ["AttachmentInputHandler"]


def __getattr__(name: str):
    if name == "AttachmentInputHandler":
        from .handler import AttachmentInputHandler

        return AttachmentInputHandler
    raise AttributeError(name)
