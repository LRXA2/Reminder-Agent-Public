from __future__ import annotations

__all__ = ["UiWizardHandler"]


def __getattr__(name: str):
    if name == "UiWizardHandler":
        from .handler import UiWizardHandler

        return UiWizardHandler
    raise AttributeError(name)
