from __future__ import annotations

from src.app.messages import msg


class TextInputCommon:
    def __init__(self, db) -> None:
        self.db = db

    def split_topics(self, topic_text: str) -> list[str]:
        seen: set[str] = set()
        result: list[str] = []
        for part in topic_text.split(","):
            value = part.strip()
            if not value:
                continue
            key = value.lower()
            if key in seen:
                continue
            seen.add(key)
            result.append(value)
        return result

    def format_missing_topics_message(self, chat_id: int, missing_topics: list[str]) -> str:
        base = msg("error_topics_missing_create", topics=", ".join(missing_topics))
        suggestions: list[str] = []
        for missing in missing_topics:
            suggestions.extend(self.db.suggest_topics_for_chat(chat_id, missing, limit=3))
        deduped: list[str] = []
        seen: set[str] = set()
        for suggestion in suggestions:
            key = suggestion.lower()
            if key in seen:
                continue
            seen.add(key)
            deduped.append(suggestion)
        if not deduped:
            return base
        return base + "\n" + msg("status_topics_suggestions", topics=", ".join(deduped[:5]))
