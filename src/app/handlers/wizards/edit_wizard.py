from __future__ import annotations

import re
from datetime import timezone
from typing import TYPE_CHECKING

from src.app.handlers.reminder_formatting import format_due_display, format_reminder_brief
from src.app.messages import msg

from .common import get_reply_target

if TYPE_CHECKING:
    from telegram import Update

    from src.app.handlers.wizards.handler import UiWizardHandler


class EditWizard:
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
        state = self.bot.pending_edit_wizards.get(chat_id)
        if not state:
            return False

        raw = (text or "").strip()
        lowered = raw.lower()
        if lowered in {"cancel", "stop"}:
            self.bot.pending_edit_wizards.pop(chat_id, None)
            await target.reply_text("Edit flow cancelled.")
            return True

        if lowered.startswith("topic_"):
            state["mode"] = "topic_menu"
            lowered = lowered.split("_", 1)[1]
        mode = str(state.get("mode") or "menu")

        if mode == "menu":
            if lowered in {"save", "done"}:
                reminder_id = int(state["id"])
                ok = self.bot.db.update_reminder_fields_for_chat(
                    reminder_id=reminder_id,
                    chat_id_to_notify=chat_id,
                    title=str(state.get("title") or "").strip(),
                    topic=str(state.get("topic") or ""),
                    notes=str(state.get("notes") or ""),
                    link=str(state.get("link") or ""),
                    priority=str(state.get("priority") or "mid"),
                    due_at_utc=str(state.get("due_at_utc") or ""),
                    recurrence_rule=str(state.get("recurrence") or ""),
                )
                if ok:
                    self.bot.db.set_reminder_topics_for_chat(reminder_id, chat_id, self.bot._split_topics(str(state.get("topic") or "")))
                    await target.reply_text(
                        format_reminder_brief(
                            reminder_id,
                            str(state.get("title") or ""),
                            str(state.get("due_at_utc") or ""),
                            self.bot.settings.default_timezone,
                        )
                    )
                    await self.bot._sync_calendar_upsert(reminder_id)
                else:
                    await target.reply_text(msg("error_update_failed", id=reminder_id))
                self.bot.pending_edit_wizards.pop(chat_id, None)
                return True

            if lowered == "topic":
                state["mode"] = "topic_menu"
                await target.reply_text(
                    "Topic options: `add`, `remove`, `replace`, `clear`, `back`.",
                    reply_markup=self.ui._edit_topic_keyboard(),
                )
                return True

            if lowered in {"title", "due", "priority", "interval", "link", "notes"}:
                state["mode"] = f"input:{lowered}"
                prompts = {
                    "title": "Enter new title (or `skip`):",
                    "due": "Enter new due date/time (`none` to clear, or `skip`):",
                    "priority": "Enter priority (`immediate|high|mid|low`, or `skip`):",
                    "interval": "Enter interval (`daily|weekly|monthly|none`, or `skip`):",
                    "link": "Enter link (`https://...`, `none` to clear, or `skip`):",
                    "notes": "Enter notes (`none` to clear, or `skip`):",
                }
                await target.reply_text(prompts[lowered])
                return True

            await target.reply_text(self.render_menu(state), reply_markup=self.ui._edit_wizard_keyboard())
            return True

        if mode == "topic_menu":
            if lowered in {"back", "menu"}:
                state["mode"] = "menu"
                await target.reply_text(self.render_menu(state), reply_markup=self.ui._edit_wizard_keyboard())
                return True
            if lowered in {"clear", "none"}:
                state["topic"] = ""
                state["mode"] = "menu"
                await target.reply_text(self.render_menu(state), reply_markup=self.ui._edit_wizard_keyboard())
                return True
            if lowered in {"add", "remove", "replace"}:
                state["mode"] = f"topic_input:{lowered}"
                await target.reply_text(f"Enter topic names to {lowered} (comma-separated), or `back`.")
                return True
            await target.reply_text(
                "Topic options: `add`, `remove`, `replace`, `clear`, `back`.",
                reply_markup=self.ui._edit_topic_keyboard(),
            )
            return True

        if mode.startswith("input:"):
            field = mode.split(":", 1)[1]
            if lowered not in {"skip", "no"}:
                if field == "title" and raw:
                    state["title"] = raw
                elif field == "due":
                    if lowered in {"none", "clear"}:
                        state["due_at_utc"] = ""
                    else:
                        due_dt, _conf = self.bot._parse_natural_datetime(raw)
                        if due_dt is None:
                            await target.reply_text("Could not parse date/time. Try again or `skip`.")
                            return True
                        state["due_at_utc"] = due_dt.astimezone(timezone.utc).isoformat()
                elif field == "priority":
                    token = {"i": "immediate", "h": "high", "m": "mid", "l": "low"}.get(lowered, lowered)
                    if token not in {"immediate", "high", "mid", "low"}:
                        await target.reply_text("Invalid priority. Try again or `skip`.")
                        return True
                    state["priority"] = token
                elif field == "interval":
                    if lowered in {"none", "clear"}:
                        state["recurrence"] = ""
                    elif lowered in {"daily", "weekly", "monthly"}:
                        if not str(state.get("due_at_utc") or ""):
                            await target.reply_text(msg("error_recurrence_requires_due"))
                            return True
                        state["recurrence"] = lowered
                    else:
                        await target.reply_text("Invalid interval. Try again or `skip`.")
                        return True
                elif field == "link":
                    if lowered in {"none", "clear"}:
                        state["link"] = ""
                    elif re.match(r"^https?://\S+$", raw, re.IGNORECASE):
                        state["link"] = raw
                    else:
                        await target.reply_text("Invalid link. Use https://... or `none`/`skip`.")
                        return True
                elif field == "notes":
                    state["notes"] = "" if lowered in {"none", "clear"} else raw

            state["mode"] = "menu"
            await target.reply_text(self.render_menu(state), reply_markup=self.ui._edit_wizard_keyboard())
            return True

        if mode.startswith("topic_input:"):
            action = mode.split(":", 1)[1]
            if lowered in {"back", "menu"}:
                state["mode"] = "topic_menu"
                await target.reply_text(
                    "Topic options: `add`, `remove`, `replace`, `clear`, `back`.",
                    reply_markup=self.ui._edit_topic_keyboard(),
                )
                return True
            topics = self.bot._split_topics(raw)
            missing_topics = self.bot.db.has_missing_topics_for_chat(chat_id, topics)
            if missing_topics:
                await target.reply_text(self.bot._format_missing_topics_message(chat_id, missing_topics))
                return True
            current_topics = self.bot._split_topics(str(state.get("topic") or ""))
            if action == "add":
                merged = current_topics + topics
                state["topic"] = ",".join(self.bot._split_topics(",".join(merged)))
            elif action == "remove":
                remove_set = {t.lower() for t in topics}
                state["topic"] = ",".join([t for t in current_topics if t.lower() not in remove_set])
            else:  # replace
                state["topic"] = ",".join(topics)
            state["mode"] = "menu"
            await target.reply_text(self.render_menu(state), reply_markup=self.ui._edit_wizard_keyboard())
            return True

        state["mode"] = "menu"
        await target.reply_text(self.render_menu(state), reply_markup=self.ui._edit_wizard_keyboard())
        return True

    def render_menu(self, state: dict[str, str]) -> str:
        due_display = format_due_display(str(state.get("due_at_utc") or ""), self.bot.settings.default_timezone)
        return (
            f"Editing #{state.get('id')}\n"
            f"- title: {state.get('title') or ''}\n"
            f"- due: {due_display}\n"
            f"- priority: {state.get('priority') or 'mid'}\n"
            f"- topic: {state.get('topic') or '(none)'}\n"
            f"- interval: {state.get('recurrence') or '(none)'}\n"
            f"- link: {state.get('link') or '(none)'}\n"
            f"- notes: {'yes' if str(state.get('notes') or '').strip() else '(none)'}\n\n"
            "Choose what to edit: `title`, `due`, `priority`, `topic`, `interval`, `link`, `notes`\n"
            "Then `save` to apply, or `cancel`."
        )
