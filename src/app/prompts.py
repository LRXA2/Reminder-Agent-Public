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


def draft_reminder_prompt(
    user_instruction: str,
    content: str,
    schema_version: str = "2",
    available_topics: list[str] | None = None,
) -> str:
    topic_line = "Available topics (use only these; otherwise use []): "
    if available_topics:
        topic_line += ", ".join(available_topics[:80])
    else:
        topic_line += "(none)"
    return (
        "You are a reminder planner. Determine if reminders are appropriate from the provided summary/content. "
        f"Return STRICT JSON ONLY using schema_version='{schema_version}' in this schema. "
        "No markdown, no prose, no code fences, JSON object only. "
        '{"schema_version":"2","appropriate": true|false, "reason": "...", "reminders": ['
        '{"title":"...","notes":"...","link":"...","priority":"immediate|high|mid|low",'
        '"due_mode":"datetime|all_day|none|unclear","due_text":"...","confidence":"high|medium|low",'
        '"topics":["..."],"priority_reason":"...","due_reason":"..."}]}. '
        "Rules: title actionable 3-12 words; "
        "do not use generic titles like Summary; only include reminders with clear actionable tasks. "
        "If due date is unclear, set due_mode to unclear and due_text to empty string. "
        "If date exists but no explicit time, use due_mode all_day. "
        "If no date exists, use due_mode none with empty due_text. "
        "If content contains a clear deadline phrase (for example 'by next friday' or 'due on 21 Mar'), do not use none/unclear; fill due_text accordingly. "
        "If actionability is weak, set appropriate to false and explain briefly in reason. "
        "topics is optional (up to 5 tags); do not invent topics and only use provided topic names; when unsure use []. "
        "priority_reason and due_reason should be short. "
        f"{topic_line}"
        f"\nUser instruction: {user_instruction}\n\nContent:\n{content[:22000]}"
    )


def repair_reminder_json_prompt(raw_text: str) -> str:
    return (
        "Fix malformed reminder output into STRICT JSON ONLY. "
        "No markdown, no prose, no code fences. Return exactly one JSON object matching schema_version '2'. "
        "If values are missing, use safe defaults.\n\n"
        f"Raw output:\n{raw_text[:22000]}"
    )


def datetime_fallback_prompt(user_text: str, timezone_name: str, now_iso: str) -> str:
    return (
        "You convert natural language date/time into a concrete due expression for a reminder app. "
        "Return STRICT JSON ONLY (no markdown/prose/code fences) with keys: due_text, due_mode, confidence. "
        "due_mode must be one of: datetime, all_day, none, unclear. "
        "confidence must be one of: high, medium, low. "
        "If no date/time intent is present, set due_mode to none and due_text to empty string. "
        "If date exists without explicit time, use due_mode all_day. "
        "If ambiguous, use due_mode unclear and low confidence. "
        "Keep due_text concise and parseable (example: '2026-03-17 09:00' or 'next monday 9am'). "
        f"Timezone: {timezone_name}. Current local time: {now_iso}. "
        f"Input: {user_text}"
    )
