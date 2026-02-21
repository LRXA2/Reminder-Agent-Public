from __future__ import annotations

import logging
from dataclasses import dataclass, field

from telegram import InlineKeyboardMarkup, Update

from src.app.handlers.reminder_draft.datetime_mixin import DraftDatetimeMixin
from src.app.handlers.reminder_draft.refinement_mixin import DraftRefinementMixin
from src.app.handlers.reminder_draft.schema_mixin import DraftSchemaMixin
from src.app.handlers.reminder_draft.session_handler import DraftSessionHandler
from src.app.handlers.operation_status import OperationStatus
from src.app.messages import msg
from src.app.prompts import draft_reminder_prompt, repair_reminder_json_prompt
from src.clients.ollama_client import OllamaClient
from src.core.config import Settings
from src.storage.database import Database

LOGGER = logging.getLogger(__name__)


@dataclass
class ReminderDraft:
    title: str
    notes: str
    link: str
    priority: str
    due_at_utc: str
    due_mode: str = "datetime"
    confidence: str = "medium"
    topics: list[str] = field(default_factory=list)
    priority_reason: str = ""
    due_reason: str = ""


@dataclass
class PendingDraftBatch:
    source_kind: str
    user_id: int
    username: str | None
    drafts: list[ReminderDraft]


class ReminderDraftManager(DraftDatetimeMixin, DraftRefinementMixin, DraftSchemaMixin):
    LONG_SUMMARY_NOTES_THRESHOLD = 250

    def __init__(self, db: Database, ollama: OllamaClient, settings: Settings, run_gpu_task, on_reminder_created=None):
        self.db = db
        self.ollama = ollama
        self.settings = settings
        self.run_gpu_task = run_gpu_task
        self.on_reminder_created = on_reminder_created
        self.pending_by_chat: dict[int, PendingDraftBatch] = {}
        self.session_handler = DraftSessionHandler(self)

    async def propose_from_text(self, update: Update, source_kind: str, content: str, user_instruction: str) -> bool:
        if not update.message or not update.effective_user:
            return False
        chat_id = update.effective_chat.id

        await OperationStatus.started(update, msg("status_draft_analyzing"))
        try:
            payload = await self._extract_drafts_from_content(chat_id, content, user_instruction)
            if not payload.get("appropriate"):
                reason = (payload.get("reason") or "No reminder-worthy items were detected.").strip()
                await OperationStatus.done(update, msg("status_draft_none", reason=reason))
                return True

            parsed_drafts = self._build_drafts(payload.get("reminders") or [], content)
            if not parsed_drafts:
                await OperationStatus.done(update, msg("status_draft_invalid"))
                return True

            self.pending_by_chat[chat_id] = PendingDraftBatch(
                source_kind=source_kind,
                user_id=update.effective_user.id,
                username=update.effective_user.username,
                drafts=parsed_drafts,
            )
            await OperationStatus.done(update, msg("status_draft_review"))
            await self._reply(update, self._render_batch(chat_id), reply_markup=self._draft_keyboard())
            return True
        except Exception as exc:
            await OperationStatus.error(update, msg("error_draft_failed", error=exc))
            return True

    async def handle_followup(self, update: Update, text: str) -> bool:
        return await self.session_handler.handle_followup(update, text)

    async def _reply(self, update: Update, text: str, reply_markup=None) -> None:
        await self.session_handler.reply(update, text, reply_markup=reply_markup)

    def _draft_keyboard(self) -> InlineKeyboardMarkup:
        return self.session_handler.draft_keyboard()

    def _apply_edit(self, chat_id: int, text: str) -> tuple[bool, str]:
        return self.session_handler.apply_edit(chat_id, text)

    def _extract_field(self, text: str, field_name: str) -> str | None:
        return self.session_handler.extract_field(text, field_name)

    def _parse_indices(self, text: str) -> list[int]:
        return self.session_handler.parse_indices(text)

    def _extract_create_topics(self, text: str) -> tuple[str, list[str]]:
        return self.session_handler.extract_create_topics(text)

    def _contains_attach_topics_flag(self, text: str) -> bool:
        return self.session_handler.contains_attach_topics_flag(text)

    def _collect_topics_from_drafts(self, drafts: list[ReminderDraft]) -> list[str]:
        return self.session_handler.collect_topics_from_drafts(drafts)

    def _select_drafts(self, drafts: list[ReminderDraft], indices: list[int]) -> list[ReminderDraft] | None:
        return self.session_handler.select_drafts(drafts, indices)

    def _render_batch(self, chat_id: int) -> str:
        return self.session_handler.render_batch(chat_id)

    async def _extract_drafts_from_content(self, chat_id: int, content: str, user_instruction: str) -> dict:
        topic_names = self.db.list_topic_names_for_chat(chat_id)
        prompt = draft_reminder_prompt(
            user_instruction,
            content,
            schema_version="2",
            available_topics=topic_names,
        )
        raw = await self.run_gpu_task(self.ollama.generate_text, prompt)
        parsed = self._parse_json_object(raw)
        normalized = self._normalize_payload(parsed) if parsed else None
        if normalized is None or not self._is_payload_valid(normalized):
            repaired_raw = await self.run_gpu_task(self.ollama.generate_text, repair_reminder_json_prompt(raw))
            repaired = self._parse_json_object(repaired_raw)
            normalized = self._normalize_payload(repaired) if repaired else None
        if normalized is None or not self._is_payload_valid(normalized):
            return {"schema_version": "2", "appropriate": False, "reason": "Could not parse reminder suggestions.", "reminders": []}
        allowed_lookup = {name.lower(): name for name in topic_names}
        if allowed_lookup:
            for reminder in normalized.get("reminders", []):
                raw_topics = reminder.get("topics")
                if not isinstance(raw_topics, list):
                    reminder["topics"] = []
                    continue
                filtered: list[str] = []
                seen: set[str] = set()
                for topic in raw_topics:
                    key = str(topic).strip().lower()
                    if not key or key not in allowed_lookup or key in seen:
                        continue
                    seen.add(key)
                    filtered.append(allowed_lookup[key])
                reminder["topics"] = filtered
        return normalized

    def _build_drafts(self, reminders: list, fallback_notes: str) -> list[ReminderDraft]:
        drafts: list[ReminderDraft] = []
        for raw in reminders:
            if not isinstance(raw, dict):
                continue
            title = str(raw.get("title") or "").strip()[:120]
            if not title:
                continue
            notes = str(raw.get("notes") or "").strip()
            if not notes:
                notes = fallback_notes
            if len(fallback_notes.strip()) >= self.LONG_SUMMARY_NOTES_THRESHOLD:
                notes = fallback_notes
            title = self._refine_generic_title(title, notes, fallback_notes, confidence=str(raw.get("confidence") or "medium"))
            link = str(raw.get("link") or "").strip()
            if not self._is_valid_link(link):
                link = ""
            if not link:
                link = self._extract_first_url(notes) or self._extract_first_url(fallback_notes) or ""
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
            due_at_utc = self._parse_due_to_utc(due_text, due_mode) if due_text else ""
            if not due_at_utc and due_mode in {"datetime", "all_day"}:
                due_mode = "none"
            if not due_at_utc and due_mode in {"none", "unclear"}:
                inferred = self._infer_due_from_text(f"{notes}\n{fallback_notes}")
                if inferred:
                    due_at_utc = inferred
                    due_mode = "all_day"
            topics_raw = raw.get("topics")
            topics = [str(t).strip() for t in topics_raw[:5]] if isinstance(topics_raw, list) else []
            topics = [t for t in topics if t]
            topics = self._filter_topics_by_relevance(topics, f"{title}\n{notes}\n{fallback_notes}")
            if confidence != "high":
                topics = []
            priority_reason = str(raw.get("priority_reason") or "").strip()[:120]
            due_reason = str(raw.get("due_reason") or "").strip()[:120]
            drafts.append(
                ReminderDraft(
                    title=title,
                    notes=notes,
                    link=link,
                    priority=priority,
                    due_at_utc=due_at_utc,
                    due_mode=due_mode,
                    confidence=confidence,
                    topics=topics,
                    priority_reason=priority_reason,
                    due_reason=due_reason,
                )
            )
        return drafts
