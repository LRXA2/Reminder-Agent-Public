from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.app.bot_orchestrator import ReminderBot


class VisionModelTagHandler:
    def __init__(self, bot: "ReminderBot") -> None:
        self.bot = bot

    def load_tags(self) -> set[str]:
        raw = self.bot.db.get_app_setting("ollama_vision_tags") or ""
        return {part.strip() for part in raw.split(",") if part.strip()}

    def save_tags(self) -> None:
        serialized = ",".join(sorted(self.bot.vision_model_tags))
        self.bot.db.set_app_setting("ollama_vision_tags", serialized)
