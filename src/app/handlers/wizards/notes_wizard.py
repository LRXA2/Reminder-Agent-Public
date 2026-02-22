from __future__ import annotations

import re
from typing import TYPE_CHECKING

from src.app.handlers.reminder_formatting import format_reminder_detail
from src.app.messages import msg

from .common import get_reply_target

if TYPE_CHECKING:
    from telegram import Update

    from src.app.handlers.wizards.handler import UiWizardHandler


class NotesWizard:
    def __init__(self, ui: "UiWizardHandler") -> None:
        self.ui = ui

    @property
    def bot(self):
        return self.ui.bot

    async def handle(self, update: "Update", text: str) -> bool:
        target = get_reply_target(update)
        if target is None:
            return False
        chat_id = update.effective_chat.id
        state = self.bot.pending_notes_wizards.get(chat_id)
        if not state:
            return False

        raw = (text or "").strip()
        lowered = raw.lower()
        if lowered in {"cancel", "stop"}:
            self.bot.pending_notes_wizards.pop(chat_id, None)
            await target.reply_text("Notes flow cancelled.")
            return True

        mode = str(state.get("mode") or "menu")
        if mode == "menu":
            if lowered == "list":
                rows = self.collect_candidates(chat_id)
                if not rows:
                    await target.reply_text(msg("error_notes_empty"))
                    return True
                lines = ["Reminders with notes:"]
                for row in rows:
                    lines.append(f"- #{row.get('id')} {row.get('title')}")
                lines.append("\nUse `view <id>`, `edit <id>`, `clear <id>`, or `cancel`.")
                await target.reply_text("\n".join(lines), reply_markup=self.ui._notes_wizard_keyboard())
                return True

            view_match = re.match(r"^view\s+(\d+)\s*$", lowered)
            if view_match:
                reminder_id = int(view_match.group(1))
                row = self.bot.db.get_reminder_by_id_for_chat(reminder_id, chat_id)
                if row is None:
                    await target.reply_text(msg("error_not_found", id=reminder_id))
                    return True
                notes = str(row["notes"] or "").strip()
                if not notes:
                    await target.reply_text(msg("error_notes_empty_for_id", id=reminder_id))
                    return True
                await target.reply_text(format_reminder_detail(dict(row), self.bot.settings.default_timezone))
                return True

            clear_match = re.match(r"^clear\s+(\d+)\s*$", lowered)
            if clear_match:
                reminder_id = int(clear_match.group(1))
                ok = await self.update_notes(chat_id, reminder_id, "")
                if ok:
                    await target.reply_text(f"Cleared notes for #{reminder_id}.")
                else:
                    await target.reply_text(msg("error_not_found", id=reminder_id))
                return True

            edit_match = re.match(r"^edit\s+(\d+)\s*$", lowered)
            if edit_match:
                reminder_id = int(edit_match.group(1))
                row = self.bot.db.get_reminder_by_id_for_chat(reminder_id, chat_id)
                if row is None:
                    await target.reply_text(msg("error_not_found", id=reminder_id))
                    return True
                state["mode"] = "edit_text"
                state["id"] = str(reminder_id)
                await target.reply_text("Send new notes text now, or `clear` to remove, or `cancel`.")
                return True

            await target.reply_text(
                "Choose: `list`, `view <id>`, `edit <id>`, `clear <id>`, or `cancel`.",
                reply_markup=self.ui._notes_wizard_keyboard(),
            )
            return True

        if mode == "edit_text":
            reminder_id = int(state.get("id") or "0")
            new_notes = "" if lowered == "clear" else raw
            ok = await self.update_notes(chat_id, reminder_id, new_notes)
            if ok:
                await target.reply_text(f"Updated notes for #{reminder_id}.")
            else:
                await target.reply_text(msg("error_not_found", id=reminder_id))
            state["mode"] = "menu"
            await target.reply_text(
                "Notes wizard. Choose: `list`, `view <id>`, `edit <id>`, `clear <id>`, or `cancel`.",
                reply_markup=self.ui._notes_wizard_keyboard(),
            )
            return True

        state["mode"] = "menu"
        return True

    def collect_candidates(self, chat_id: int) -> list[dict]:
        rows = self.bot.db.list_reminders_for_chat(chat_id)
        with_notes: list[dict] = []
        for row in rows[:120]:
            detail = self.bot.db.get_reminder_by_id_for_chat(int(row["id"]), chat_id)
            if detail is None:
                continue
            detail_dict = dict(detail)
            if not self.bot.reminder_logic_handler.is_notes_list_candidate(detail_dict):
                continue
            with_notes.append(detail_dict)
            if len(with_notes) >= 20:
                break
        return with_notes

    async def update_notes(self, chat_id: int, reminder_id: int, notes: str) -> bool:
        existing = self.bot.db.get_reminder_by_id_for_chat(reminder_id, chat_id)
        if existing is None:
            return False
        row = dict(existing)
        ok = self.bot.db.update_reminder_fields_for_chat(
            reminder_id=reminder_id,
            chat_id_to_notify=chat_id,
            title=str(row.get("title") or ""),
            topic=str(row.get("topic") or ""),
            notes=notes,
            link=str(row.get("link") or ""),
            priority=str(row.get("priority") or "mid"),
            due_at_utc=str(row.get("due_at_utc") or ""),
            recurrence_rule=str(row.get("recurrence_rule") or ""),
        )
        if ok:
            await self.bot.calendar_sync_handler.sync_calendar_upsert(reminder_id)
        return ok
