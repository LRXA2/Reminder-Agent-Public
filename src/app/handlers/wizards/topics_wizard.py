from __future__ import annotations

import re
from typing import TYPE_CHECKING

from src.app.messages import msg

from .common import get_reply_target

if TYPE_CHECKING:
    from telegram import Update

    from src.app.handlers.wizards.handler import UiWizardHandler


class TopicsWizard:
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
        state = self.bot.pending_topics_wizards.get(chat_id)
        if not state:
            return False

        raw = (text or "").strip()
        lowered = raw.lower()
        if lowered in {"cancel", "stop"}:
            self.bot.pending_topics_wizards.pop(chat_id, None)
            await target.reply_text("Topics flow cancelled.")
            return True

        if lowered in {"list", "list all"}:
            include_archived = lowered == "list all"
            rows = self.bot.db.list_topic_index_for_chat(chat_id, include_archived=include_archived)
            if not rows:
                await target.reply_text(msg("error_topics_empty"))
                return True
            lines = ["Topics (open + archived):" if include_archived else "Topics (open only):"]
            for idx, row in enumerate(rows, start=1):
                topic_id = int(row["id"])
                topic = str(row["display_name"] or "").strip()
                internal_name = str(row["internal_name"] or "").strip()
                open_count = int(row["open_count"] or 0)
                archived_count = int(row["archived_count"] or 0)
                if include_archived:
                    lines.append(f"{idx}) [{topic_id}] {topic} ({internal_name}) - open:{open_count}, archived:{archived_count}")
                else:
                    lines.append(f"{idx}) [{topic_id}] {topic} - open:{open_count}")
            await target.reply_text("\n".join(lines), reply_markup=self.ui._topics_wizard_keyboard())
            return True

        create_match = re.match(r"^create\s+(.+)$", raw, re.IGNORECASE)
        if create_match:
            name = create_match.group(1).strip()
            if not name:
                await target.reply_text(msg("usage_topics_create"))
                return True
            self.bot.db.create_topic_for_chat(chat_id, name)
            await target.reply_text(msg("status_topic_created", topic=name))
            return True

        rename_match = re.match(r"^rename\s+(\d+)\s+(.+)$", raw, re.IGNORECASE)
        if rename_match:
            topic_id = int(rename_match.group(1))
            new_name = rename_match.group(2).strip()
            ok = self.bot.db.rename_topic_for_chat(chat_id, topic_id, new_name)
            await target.reply_text(msg("status_topic_renamed", id=topic_id, topic=new_name) if ok else msg("error_topic_not_found", id=topic_id))
            return True

        delete_match = re.match(r"^delete\s+(\d+)\s*$", lowered)
        if delete_match:
            topic_id = int(delete_match.group(1))
            ok = self.bot.db.delete_topic_for_chat(chat_id, topic_id)
            await target.reply_text(msg("status_topic_deleted", id=topic_id) if ok else msg("error_topic_not_found", id=topic_id))
            return True

        merge_match = re.match(r"^merge\s+(\d+)\s+(\d+)\s*$", lowered)
        if merge_match:
            from_id = int(merge_match.group(1))
            to_id = int(merge_match.group(2))
            ok = self.bot.db.merge_topics_for_chat(chat_id, from_id, to_id)
            await target.reply_text(msg("status_topics_merged", from_id=from_id, to_id=to_id) if ok else msg("error_topics_merge_failed"))
            return True

        await target.reply_text(
            "Topics wizard commands: `list`, `list all`, `create <name>`, `rename <id> <new>`, `delete <id>`, `merge <from> <to>`, `cancel`.",
            reply_markup=self.ui._topics_wizard_keyboard(),
        )
        return True
