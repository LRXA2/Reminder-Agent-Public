from __future__ import annotations

import re
from typing import TYPE_CHECKING

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update

from src.app.handlers.operation_status import OperationStatus
from src.app.handlers.reminder_formatting import format_due_display, format_reminder_brief
from src.app.messages import msg

if TYPE_CHECKING:
    from src.app.handlers.reminder_draft.manager import ReminderDraft, ReminderDraftManager


class DraftSessionHandler:
    def __init__(self, manager: "ReminderDraftManager") -> None:
        self.manager = manager

    async def handle_followup(self, update: Update, text: str) -> bool:
        if not update.message:
            return False
        chat_id = update.effective_chat.id
        batch = self.manager.pending_by_chat.get(chat_id)
        if not batch:
            return False

        stripped = text.strip()
        lowered = stripped.lower()
        if lowered == "1":
            lowered = "confirm"
            stripped = "confirm"
        elif lowered == "2":
            lowered = "confirm topics"
            stripped = "confirm topics"
        elif lowered == "3":
            lowered = "show"
            stripped = "show"
        elif lowered == "4":
            lowered = "cancel"
            stripped = "cancel"
        elif re.fullmatch(r"s\s+\d+(\s*,\s*\d+)*", lowered):
            stripped = "confirm " + re.sub(r"^s\s+", "", stripped, flags=re.IGNORECASE)
            lowered = stripped.lower()
        elif re.fullmatch(r"t\s+\d+(\s*,\s*\d+)*", lowered):
            stripped = "confirm topics " + re.sub(r"^t\s+", "", stripped, flags=re.IGNORECASE)
            lowered = stripped.lower()
        elif re.fullmatch(r"r\s+\d+(\s*,\s*\d+)*", lowered):
            stripped = "remove " + re.sub(r"^r\s+", "", stripped, flags=re.IGNORECASE)
            lowered = stripped.lower()
        elif lowered.startswith("e "):
            stripped = "edit " + stripped[2:].strip()
            lowered = stripped.lower()
        confirm_aliases = {
            "add as reminder",
            "add as reminders",
            "save reminder",
            "save reminders",
            "save all",
            "save all reminders",
            "create reminder",
            "create reminders",
        }
        if lowered in {"cancel", "skip", "discard", "no"}:
            self.manager.pending_by_chat.pop(chat_id, None)
            await self.reply(update, msg("draft_discarded"))
            return True

        if lowered in {"show", "list", "preview"}:
            await self.reply(update, self.render_batch(chat_id), reply_markup=self.draft_keyboard())
            return True

        if lowered == "yes" or lowered.startswith("confirm") or lowered in confirm_aliases:
            confirm_base, create_topics = self.extract_create_topics(stripped)
            attach_topics = self.contains_attach_topics_flag(confirm_base)
            normalized_confirm_base = re.sub(r"\btopics\b", " ", confirm_base, flags=re.IGNORECASE)
            indices = self.parse_indices(normalized_confirm_base.lower())
            selected = self.select_drafts(batch.drafts, indices)
            if selected is None:
                await self.reply(update, msg("draft_invalid_selection"))
                return True
            if not selected:
                self.manager.pending_by_chat.pop(chat_id, None)
                await self.reply(update, msg("status_draft_invalid"))
                return True

            if attach_topics:
                selected_topics = self.collect_topics_from_drafts(selected)
                missing_topics = self.manager.db.has_missing_topics_for_chat(chat_id, selected_topics)
                if missing_topics:
                    create_lookup = {topic.strip().lower() for topic in create_topics if topic.strip()}
                    can_create_all = all(topic.lower() in create_lookup for topic in missing_topics)
                    if not can_create_all:
                        await self.reply(
                            update,
                            "Missing topics for selected drafts: "
                            f"{', '.join(missing_topics)}. "
                            "Create and continue with: confirm topics create:" + ",".join(missing_topics),
                        )
                        return True
                    for topic_name in missing_topics:
                        self.manager.db.create_topic_for_chat(chat_id, topic_name)

            missing = [
                i + 1
                for i, draft in enumerate(selected)
                if not draft.title.strip()
                or draft.priority not in {"immediate", "high", "mid", "low"}
                or draft.due_mode == "unclear"
                or (draft.due_mode in {"datetime", "all_day"} and not draft.due_at_utc)
            ]
            if missing:
                await self.reply(
                    update,
                    "Some selected drafts still need clarification (title/priority/due). "
                    "Use: edit <n> title:<...> p:<...> at:<...>. For no due date use at:none.",
                )
                return True

            await OperationStatus.started(update, msg("status_draft_saving"))
            try:
                lines: list[str] = []
                for draft in selected:
                    app_user_id = self.manager.db.upsert_user(batch.user_id, batch.username, self.manager.settings.default_timezone)
                    reminder_id = self.manager.db.create_reminder(
                        user_id=app_user_id,
                        source_message_id=None,
                        source_kind=batch.source_kind,
                        title=draft.title,
                        topic="",
                        notes=draft.notes,
                        link=draft.link,
                        priority=draft.priority,
                        due_at_utc=draft.due_at_utc,
                        timezone_name=self.manager.settings.default_timezone,
                        chat_id_to_notify=chat_id,
                        recurrence_rule=None,
                    )
                    if attach_topics and draft.topics:
                        self.manager.db.set_reminder_topics_for_chat(reminder_id, chat_id, draft.topics)
                    lines.append(
                        format_reminder_brief(reminder_id, draft.title, draft.due_at_utc, self.manager.settings.default_timezone)
                    )
                    if self.manager.on_reminder_created:
                        await self.manager.on_reminder_created(reminder_id)

                self.manager.pending_by_chat.pop(chat_id, None)
                await OperationStatus.done(update, msg("status_draft_saved"))
                await self.reply(update, "\n\n".join(lines))
                if not attach_topics and any(draft.topics for draft in selected):
                    await self.reply(
                        update,
                        "Saved without topic auto-attach. Use `confirm topics` next time to attach suggested topics.",
                    )
            except Exception as exc:
                await OperationStatus.error(update, msg("error_draft_save_failed", error=exc))
            return True

        if lowered.startswith("remove "):
            indices = self.parse_indices(lowered)
            if not indices:
                await self.reply(update, msg("draft_remove_usage"))
                return True
            new_drafts = [draft for i, draft in enumerate(batch.drafts, start=1) if i not in set(indices)]
            if not new_drafts:
                self.manager.pending_by_chat.pop(chat_id, None)
                await self.reply(update, msg("draft_removed_all"))
                return True
            batch.drafts = new_drafts
            await self.reply(update, self.render_batch(chat_id), reply_markup=self.draft_keyboard())
            return True

        if lowered.startswith("edit "):
            ok, message = self.apply_edit(chat_id, text)
            await self.reply(update, message)
            return True

        await self.reply(
            update,
            "I still have draft reminders waiting.\n"
            "Try one of these:\n"
            "- 1 (save all)\n"
            "- 2 (save all + attach topics)\n"
            "- s 1,3 (save selected)\n"
            "- t 1,3 (save selected + topics)\n"
            "- e 2 title:... p:high at:tomorrow 9am\n"
            "- r 2\n"
            "- 3 (show)\n"
            "- 4 (cancel)",
        )
        return True

    async def reply(self, update: Update, text: str, reply_markup=None) -> None:
        message = update.message or (update.callback_query.message if update.callback_query else None)
        if message is None:
            return
        await message.reply_text(text, reply_markup=reply_markup)

    def draft_keyboard(self) -> InlineKeyboardMarkup:
        rows = [
            [InlineKeyboardButton("Save All", callback_data="draft:save"), InlineKeyboardButton("Save + Topics", callback_data="draft:topics")],
            [InlineKeyboardButton("Show", callback_data="draft:show"), InlineKeyboardButton("Cancel", callback_data="draft:cancel")],
        ]
        return InlineKeyboardMarkup(rows)

    def apply_edit(self, chat_id: int, text: str) -> tuple[bool, str]:
        batch = self.manager.pending_by_chat.get(chat_id)
        if not batch:
            return False, "No pending drafts."

        match = re.match(r"edit\s+(\d+)\s+(.+)$", text.strip(), re.IGNORECASE)
        if not match:
            return False, "Usage: edit <n> title:<...> p:<...> at:<...> notes:<...> link:<...>"

        idx = int(match.group(1))
        if idx < 1 or idx > len(batch.drafts):
            return False, "Draft index out of range."
        edits_text = match.group(2)
        draft = batch.drafts[idx - 1]

        title = self.extract_field(edits_text, "title")
        notes = self.extract_field(edits_text, "notes")
        link = self.extract_field(edits_text, "link")
        priority_match = re.search(r"(?:p|priority)\s*:\s*(immediate|high|mid|low)\b", edits_text, re.IGNORECASE)
        at_match = re.search(r"at\s*:\s*(.+?)(?=\s+(?:title|notes|link|p|priority)\s*:|$)", edits_text, re.IGNORECASE)

        if title is not None:
            draft.title = title
        if notes is not None:
            draft.notes = notes
        if link is not None:
            draft.link = link
        if priority_match:
            draft.priority = priority_match.group(1).lower()
        if at_match:
            due_text = at_match.group(1).strip()
            if due_text.lower() in {"none", "no due", "someday", "backlog"}:
                draft.due_mode = "none"
                draft.due_at_utc = ""
            else:
                due = self.manager._parse_due_to_utc(due_text)
                if not due:
                    return False, msg("error_draft_invalid_at")
                draft.due_mode = "datetime"
                draft.due_at_utc = due

        return True, self.render_batch(chat_id)

    def extract_field(self, text: str, field_name: str) -> str | None:
        pattern = rf"{field_name}\s*:\s*(.+?)(?=\s+(?:title|notes|link|p|priority|at)\s*:|$)"
        match = re.search(pattern, text, re.IGNORECASE)
        if not match:
            return None
        return match.group(1).strip()

    def parse_indices(self, text: str) -> list[int]:
        numbers = re.findall(r"\d+", text)
        return [int(n) for n in numbers] if numbers else []

    def extract_create_topics(self, text: str) -> tuple[str, list[str]]:
        match = re.search(r"\bcreate\s*:\s*(.+)$", text, re.IGNORECASE)
        if not match:
            return text, []
        raw_topics = match.group(1).strip()
        topics = [part.strip() for part in raw_topics.split(",") if part.strip()]
        base = text[: match.start()].strip()
        return base, topics

    def contains_attach_topics_flag(self, text: str) -> bool:
        return bool(re.search(r"\btopics\b", text or "", re.IGNORECASE))

    def collect_topics_from_drafts(self, drafts: list["ReminderDraft"]) -> list[str]:
        seen: set[str] = set()
        collected: list[str] = []
        for draft in drafts:
            for topic in draft.topics:
                normalized = topic.strip()
                if not normalized:
                    continue
                key = normalized.lower()
                if key in seen:
                    continue
                seen.add(key)
                collected.append(normalized)
        return collected

    def select_drafts(self, drafts: list["ReminderDraft"], indices: list[int]) -> list["ReminderDraft"] | None:
        if not indices:
            return drafts
        selected: list["ReminderDraft"] = []
        for idx in indices:
            if idx < 1 or idx > len(drafts):
                return None
            selected.append(drafts[idx - 1])
        return selected

    def render_batch(self, chat_id: int) -> str:
        batch = self.manager.pending_by_chat[chat_id]
        lines = [f"Proposed reminders ({len(batch.drafts)}) - not saved yet"]
        for i, draft in enumerate(batch.drafts, start=1):
            due_display = format_due_display(draft.due_at_utc, self.manager.settings.default_timezone)
            if draft.due_mode == "none":
                due_display = "(none)"
            elif draft.due_mode == "unclear":
                due_display = "(needs date/time)"
            confidence_flag = f" | confidence:{draft.confidence}" if draft.confidence in {"medium", "low"} else ""
            lines.append(f"{i}) {draft.title}")
            lines.append(f"   Due: {due_display}")
            lines.append(f"   Priority: {draft.priority}{confidence_flag}")
            if draft.link:
                lines.append(f"   Link: {draft.link}")
            if draft.notes.strip():
                notes_preview = draft.notes.strip().splitlines()[0]
                if len(notes_preview) > 100:
                    notes_preview = notes_preview[:100].rstrip() + "..."
                lines.append(f"   Notes: yes ({len(draft.notes)} chars)")
                lines.append(f"   Notes preview: {notes_preview}")
            if draft.topics:
                lines.append(f"   Topics: {', '.join(draft.topics)}")
            lines.append("")
        lines.append("Actions")
        lines.append("1 = save all")
        lines.append("2 = save all + attach topics")
        lines.append("3 = show again")
        lines.append("4 = cancel")
        lines.append("s 1,3 = save selected")
        lines.append("t 1,3 = save selected + topics")
        lines.append("e 2 title:Submit concept p:high at:28 feb 11am = edit one")
        lines.append("r 2 = remove one")
        lines.append("confirm topics create:work,personal = create missing topics + save")
        return "\n".join(lines)
