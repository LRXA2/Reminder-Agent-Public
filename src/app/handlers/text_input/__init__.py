from __future__ import annotations

__all__ = ["TextInputHandler"]


def __getattr__(name: str):
    if name == "TextInputHandler":
        from .handler import TextInputHandler

        return TextInputHandler
    raise AttributeError(name)
