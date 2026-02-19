from __future__ import annotations


def group_summary_prompt(lines: list[str]) -> str:
    return (
        "You summarize Telegram group updates. Return concise markdown with sections: "
        "Key updates, Decisions, Action items, Links, Open questions. "
        "If links are present in messages, include them under Links. Keep it practical and brief.\n\n"
        "Messages:\n"
        + "\n".join(lines)
    )


def image_summary_prompt(user_instruction: str) -> str:
    return (
        "Summarize this image for a productivity assistant. "
        "Return concise plain text with key details, dates/deadlines, and possible follow-up actions if visible. "
        f"User request context: {user_instruction or 'No extra instructions provided.'}"
    )


def image_reminder_extract_prompt(user_instruction: str) -> str:
    return (
        "You extract reminder details from an image for a personal productivity bot. "
        "Return STRICT JSON ONLY with keys: title, notes. "
        "Rules: title must be actionable, 3-12 words, and must NOT be a generic heading like 'Summary'. "
        "notes must be <= 280 chars. "
        "If uncertain, infer the most likely concrete task from visible content. "
        f"User context: {user_instruction or 'No extra instructions provided.'}"
    )


def hackathon_query_prompt(user_query: str, corpus_lines: list[str]) -> str:
    return (
        "You are an assistant that extracts hackathon opportunities from chat history. "
        "Use ONLY the content provided. If date range is requested, filter accordingly. "
        "If unknown, say unknown. Return concise bullet points with: Name, Date/Time, Location, Link (if any).\n\n"
        f"User question:\n{user_query}\n\n"
        "Chat history:\n"
        + "\n".join(corpus_lines[-180:])
    )


def audio_transcript_summary_prompt(user_text: str, transcript: str) -> str:
    return (
        "Summarize this transcript for reminders. Return concise markdown with Key points, "
        "Deadlines/Dates, and Action items.\n\n"
        f"User request: {user_text}\n\nTranscript:\n{transcript[:22000]}"
    )


def document_summary_prompt(kind: str, user_instruction: str, excerpt: str) -> str:
    return (
        f"Summarize this {kind.upper()} for a reminder assistant. "
        "Return concise markdown with: Key points, Deadlines/Dates, Action items. "
        f"User request: {user_instruction}\n\n"
        "Document content:\n"
        f"{excerpt}"
    )


def document_reminder_extract_prompt(kind: str, user_instruction: str, excerpt: str) -> str:
    return (
        f"Extract a single best reminder from this {kind.upper()} and return STRICT JSON only. "
        'Format: {"title": "...", "notes": "..."}. '
        "Rules: title 3-12 words, actionable, and not a generic heading like 'Summary'; notes <= 280 chars. "
        f"User instruction: {user_instruction}\n\n"
        "Document content:\n"
        f"{excerpt}"
    )


def draft_reminder_prompt(user_instruction: str, content: str) -> str:
    return (
        "You are a reminder planner. Determine if reminders are appropriate from the provided summary/content. "
        "Return STRICT JSON ONLY in this schema: "
        '{"appropriate": true|false, "reason": "...", "reminders": ['
        '{"title":"...","notes":"...","link":"...","priority":"immediate|high|mid|low","due_text":"..."}]}. '
        "Rules: suggest 0-5 reminders max; title actionable 3-12 words; notes <= 280 chars; "
        "do not use generic titles like Summary; only include reminders with clear actionable tasks. "
        "If due date is unclear, set due_text to empty string."
        f"\nUser instruction: {user_instruction}\n\nContent:\n{content[:22000]}"
    )
