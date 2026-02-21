from __future__ import annotations

import json
import re


class DraftSchemaMixin:
    def _normalize_payload(self, parsed: dict) -> dict:
        if not isinstance(parsed, dict):
            return {
                "schema_version": "2",
                "appropriate": False,
                "reason": "",
                "reminders": [],
            }
        reminders_raw = parsed.get("reminders")
        reminders = reminders_raw if isinstance(reminders_raw, list) else []

        normalized_reminders: list[dict] = []
        for raw in reminders:
            if not isinstance(raw, dict):
                continue
            title = str(raw.get("title") or "").strip()[:120]
            notes = str(raw.get("notes") or "").strip()[:280]
            link = str(raw.get("link") or "").strip()
            if not self._is_valid_link(link):
                link = ""

            priority = str(raw.get("priority") or "mid").lower().strip()
            if priority not in {"immediate", "high", "mid", "low"}:
                priority = "mid"

            due_mode = str(raw.get("due_mode") or "datetime").lower().strip()
            if due_mode not in {"datetime", "all_day", "none", "unclear"}:
                due_mode = "datetime"

            confidence = str(raw.get("confidence") or "medium").lower().strip()
            if confidence not in {"high", "medium", "low"}:
                confidence = "medium"

            due_text = str(raw.get("due_text") or "").strip()
            topics_raw = raw.get("topics")
            topics = [str(t).strip() for t in topics_raw[:3]] if isinstance(topics_raw, list) else []
            topics = [t for t in topics if t]

            normalized_reminders.append(
                {
                    "title": title,
                    "notes": notes,
                    "link": link,
                    "priority": priority,
                    "due_mode": due_mode,
                    "due_text": due_text,
                    "confidence": confidence,
                    "topics": topics,
                    "priority_reason": str(raw.get("priority_reason") or "").strip()[:120],
                    "due_reason": str(raw.get("due_reason") or "").strip()[:120],
                }
            )

        schema_version = str(parsed.get("schema_version") or "1").strip() or "1"
        return {
            "schema_version": schema_version,
            "appropriate": bool(parsed.get("appropriate")),
            "reason": str(parsed.get("reason") or "").strip(),
            "reminders": normalized_reminders,
        }

    def _is_payload_valid(self, payload: dict) -> bool:
        if not isinstance(payload, dict):
            return False
        if str(payload.get("schema_version") or "") != "2":
            return False
        reminders = payload.get("reminders")
        if not isinstance(reminders, list):
            return False
        appropriate = bool(payload.get("appropriate"))
        if appropriate and not reminders:
            return False
        return True

    def _parse_json_object(self, text: str) -> dict | None:
        try:
            loaded = json.loads(text)
            if isinstance(loaded, dict):
                return loaded
        except Exception:
            pass

        match = re.search(r"\{[\s\S]*\}", text)
        if not match:
            return None
        try:
            loaded = json.loads(match.group(0))
        except Exception:
            return None
        return loaded if isinstance(loaded, dict) else None
