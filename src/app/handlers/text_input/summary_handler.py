from __future__ import annotations

import asyncio

from src.app.handlers.intent_parsing import extract_summary_content
from src.app.handlers.operation_status import OperationStatus
from src.app.messages import msg
from src.app.prompts import hackathon_query_prompt


class TextSummaryHandler:
    def __init__(self, parent) -> None:
        self.parent = parent

    async def handle_summary_intent(self, update, text: str, build_group_summary) -> None:
        if not update.message or not update.effective_user:
            return

        await OperationStatus.started(update, msg("status_text_summary_started"))
        await asyncio.sleep(0.15)
        try:
            inline_content = extract_summary_content(text)
            replied_content = ""
            replied = update.message.reply_to_message
            if replied:
                replied_content = self.message_text_with_links(replied)

            if self.is_deictic_summary_text(inline_content) and replied_content:
                inline_content = ""

            if inline_content:
                summary_source = inline_content
            elif replied_content:
                summary_source = replied_content
            else:
                summary_source = ""

            if summary_source:
                summary = await self.parent.run_gpu_task(self.summarize_inline_text, summary_source)
            elif self.parent.settings.monitored_group_chat_id:
                summary = await build_group_summary(save=True)
            else:
                await update.message.reply_text(
                    "Please paste the content after your summarize request, or set MONITORED_GROUP_CHAT_ID for group summaries."
                )
                return

            await update.message.reply_text(summary)
            if self.is_non_summary_response(summary):
                return
            await OperationStatus.done(update, msg("status_summary_done"))
            await self.parent.draft_manager.propose_from_text(
                update=update,
                source_kind="group_summary",
                content=summary,
                user_instruction=text if inline_content else (replied_content or text),
            )
        except Exception as exc:
            await OperationStatus.error(update, msg("error_text_summary_failed", error=exc))

    def summarize_inline_text(self, content: str) -> str:
        cleaned_lines = []
        for raw_line in content.splitlines():
            line = raw_line.strip()
            if not line:
                continue
            if len(line) > 700:
                line = line[:700] + "..."
            cleaned_lines.append(line)
        if not cleaned_lines:
            return "I did not find enough text to summarize."
        return self.parent.ollama.summarize_messages(cleaned_lines)

    def is_deictic_summary_text(self, text: str) -> bool:
        lowered = (text or "").strip().lower()
        return lowered in {"this", "that", "it", "this one", "that one", "this message", "that message"}

    def is_non_summary_response(self, summary: str) -> bool:
        lowered = (summary or "").strip().lower()
        if not lowered:
            return True
        blockers = (
            "please provide",
            "please share",
            "messages you'd like me to summarize",
            "could you provide",
            "i need the messages",
            "i did not find enough text to summarize",
        )
        return any(marker in lowered for marker in blockers)

    async def handle_hackathon_query(self, update, user_query: str) -> None:
        if not update.message:
            return
        rows = self.parent.db.fetch_recent_chat_messages(update.effective_chat.id, limit=300)
        if not rows:
            await update.message.reply_text(msg("hackathon_no_history"))
            return

        corpus_lines: list[str] = []
        for row in reversed(rows):
            text = (row["text"] or "").strip()
            if not text:
                continue
            if len(text) > 1000:
                text = text[:1000] + "..."
            corpus_lines.append(f"[{row['received_at_utc']}] {text}")

        if not corpus_lines:
            await update.message.reply_text(msg("hackathon_no_text"))
            return

        prompt = hackathon_query_prompt(user_query, corpus_lines)
        answer = await self.parent.run_gpu_task(self.parent.ollama.generate_text, prompt)
        await update.message.reply_text(answer)

    def message_text_with_links(self, message) -> str:
        import re

        base_text = (getattr(message, "text", None) or getattr(message, "caption", None) or "").strip()
        if not base_text:
            return ""

        entities = list(getattr(message, "entities", None) or []) + list(getattr(message, "caption_entities", None) or [])
        urls: list[str] = []
        for entity in entities:
            entity_type = str(getattr(entity, "type", "")).lower()
            if "text_link" in entity_type:
                url = str(getattr(entity, "url", "") or "").strip()
                if url:
                    urls.append(url)
                continue
            if entity_type.endswith("url") or entity_type == "url":
                start = int(getattr(entity, "offset", 0) or 0)
                length = int(getattr(entity, "length", 0) or 0)
                if length > 0 and 0 <= start < len(base_text):
                    candidate = base_text[start : start + length].strip()
                    if candidate.startswith("www."):
                        candidate = "https://" + candidate
                    if re.match(r"^https?://\S+$", candidate, re.IGNORECASE):
                        urls.append(candidate)

        deduped: list[str] = []
        seen: set[str] = set()
        for url in urls:
            key = url.lower()
            if key in seen:
                continue
            seen.add(key)
            deduped.append(url)
        if not deduped:
            return base_text
        return base_text + "\n\nLinks:\n" + "\n".join(deduped)
