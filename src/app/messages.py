from __future__ import annotations

HELP_TEXT = """Reminder Bot Commands

/help
Show this help message

/add <task> [link:<url>] [p:immediate|high|mid|low] [at:<time>] [every:daily|weekly|monthly]
Examples:
- /add Pay rent p:high at:tomorrow 9am
- /add Submit form link:https://example.com p:mid at:fri 5pm

/edit <id> [title:<text>] [p:<priority>] [at:<datetime>] [notes:<text>] [link:<url>] [every:daily|weekly|monthly|none]
Examples:
- /edit 12 p:high at:tomorrow 9am
- /edit 12 title:Review ASAVC notes:Bring Smart No.4

/done <id>
Example: /done 12

/delete <id>
Permanently delete reminder

/detail <id>
Show full reminder details

/list all
/list <chat_id>
/list priority <immediate|high|mid|low>
/list due <Nd>
Examples:
- /list due 14d
- /list -1002219388089
/list today
/list tomorrow
/list overdue

/summary
Summarize recent monitored group messages

/summary <chat_id>
Summarize recent messages for a specific tracked group/channel

You can also type natural language like:
- summarize for me
- help me summarize
- summarize it for me and remind me tomorrow 9am p:high
- reply to a message: add as reminder high tomorrow 9am
- reply to a reminder card: set to tomorrow 8am high
- what hackathons are available on 1 Mar - 15 Mar

Reminder response format:
- ID: <id>
- Title: <title>
- Date: dd/mm/yy [HH:mm]
Use /detail <id> for full notes/details.

/models
List installed Ollama models

/model
Show active text/vision models

/model <name>
Set active text model (backward-compatible)

/model text <name>
Set active text model

/model vision <name>
Set active vision model

/model tag <name> vision
Tag a model as vision-capable

/model untag <name> vision
Remove a model's vision-capable tag

/status
Show Ollama and GPU status

Image reminder flow:
- send an image
- add a caption with reminder details, or reply to the image (example: remind me high tomorrow 9am)

Audio reminder flow:
- send audio/voice with caption, or reply to audio/voice
- examples: summarize this audio / create reminders from this recording

Draft confirmation flow for summaries/images/documents:
- bot proposes one or more reminders first
- you review/edit before saving
- reply with: confirm | confirm 1,3 | edit <n> ... | remove <n> | cancel

Document flow (DOCX/PDF):
- send a DOCX or PDF with caption, or reply to one
- examples: summarize this document / create reminder low tomorrow 9am
"""


MESSAGES = {
    "usage_add": "Usage: /add <task> [link:<url>] [p:high] [at:tomorrow 9am] [every:daily|weekly|monthly]",
    "usage_done": "Usage: /done <id>",
    "usage_edit": "Usage: /edit <id> [title:<text>] [p:<priority>] [at:<datetime>] [notes:<text>] [link:<url>] [every:daily|weekly|monthly|none]",
    "usage_delete": "Usage: /delete <id>",
    "usage_detail": "Usage: /detail <id>",
    "usage_list": "Usage: /list all | /list <chat_id> | /list priority high | /list due 14d | /list today|tomorrow|overdue",
    "usage_summary": "Usage: /summary OR /summary <chat_id>",
    "usage_list_due": "Use day format like: /list due 14d",
    "usage_model_tag": "Usage: /model tag <name> vision OR /model untag <name> vision",
    "error_id_number": "Reminder id must be a number.",
    "error_title_empty": "Title cannot be empty.",
    "error_due_empty": "Due date/time cannot be empty.",
    "error_edit_no_fields": "Please provide fields to update.",
    "error_list_unknown": "Unknown list filter. Try /list all, /list <chat_id>, /list due 14d, or /help",
    "error_list_invalid": "Invalid list value. Try /help",
    "error_list_empty": "No matching open reminders.",
    "error_summary_target": "No summary target configured. Set MONITORED_GROUP_CHAT_ID or pass /summary <chat_id>.",
    "status_summary_start": "Got it - summarizing now...",
    "status_summary_done": "Summary complete. I will now draft reminder suggestions.",
    "error_models_empty": "No Ollama models found. Pull one with: ollama pull <model>",
    "error_model_not_installed": "Model not installed. Run: ollama pull {model}",
    "status_model_tagged": "Tagged as vision-capable: {model}",
    "status_model_untagged": "Removed vision tag: {model}",
    "usage_model_role": "Usage: /model {role} <name>",
    "status_model_set_vision": "Active vision model set to: {model}",
    "status_model_set_text": "Active text model set to: {model}",
    "error_text_only_reply": "I can only create reply-based reminders from text/caption messages right now.",
    "error_edit_must_keep": "Edited reminder must keep a non-empty title and due date/time.",
    "error_download_image": "I could not download that image. Please try again.",
    "error_download_doc": "I could not download that document. Please try again.",
    "error_download_audio": "I could not download that audio file. Please try again.",
    "status_extract_mp4": "Extracting audio track from MP4...",
    "draft_discarded": "Okay, discarded draft reminders.",
    "draft_invalid_selection": "Invalid draft selection. Example: confirm 1,3",
    "draft_remove_usage": "Usage: remove <n> or remove 1,3",
    "draft_removed_all": "All draft reminders removed.",
    "hackathon_no_history": "I do not have message history yet. Paste or forward hackathon posts first.",
    "hackathon_no_text": "I do not have enough text content to answer that yet.",
    "status_done_archived": "Reminder #{id} archived.",
    "error_done_not_found": "Reminder #{id} not found or already archived.",
    "status_deleted": "Reminder #{id} permanently deleted.",
    "error_not_found": "Reminder #{id} not found.",
    "error_update_failed": "Reminder #{id} could not be updated.",
    "error_summary_run": "I hit an error while running summary: {error}",
}


def msg(key: str, **kwargs: object) -> str:
    template = MESSAGES[key]
    return template.format(**kwargs)
