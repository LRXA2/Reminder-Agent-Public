from __future__ import annotations

HELP_TEXT = """Reminder Bot Help

Use /help <topic> or tap a topic button.

Available topics:
- reminders
- notes
- summaries
- files
- models
- sync
- examples
"""


HELP_TOPICS = {
    "reminders": """Reminders

/add <task>
Starts guided add flow (due -> priority -> topic -> interval -> link/notes).

Quick inline form also works:
/add <task> [topic:<a,b>|#tag] [link:<url>] [p:immediate|high|mid|low|!h] [at:<time|none>] [every:daily|weekly|monthly|@daily]
- Create topics first: /topics create <name>
- If REQUIRE_TOPIC_ON_ADD=true, topic is mandatory.
- /add Pay rent p:high at:tomorrow 9am
- /add Read article p:low at:none
- /add Submit report topic:work at:16 Mar
- /add Team meeting prep #work 16 Mar !h

/edit <id>
Starts guided edit flow (title -> due -> priority -> topic -> interval -> link/notes).

Quick inline form also works:
/edit <id> [title:<text>] [topic:<a,b>|topic:+a|topic:-a|topic:none] [p:<priority>] [at:<datetime|none>] [notes:<text>] [link:<url>] [every:daily|weekly|monthly|none]
/done <id>
/delete <id>
/delete
Starts delete wizard.
/notes
/notes <id>

/list all
/list <chat_id>
/list priority <immediate|high|mid|low>
/list topic <name>
/list archived
/list archived topic <name>
/topics
Starts topics wizard.
/topics all
/topics create <name>
/topics rename <id> <new name>
/topics delete <id>
/topics merge <from_id> <to_id>
/topic add <reminder_id> <name>
/topic remove <reminder_id> <name>
/topics all   (shows topic IDs + internal names)
/list due <Nd>
/list today|tomorrow|overdue
""",
    "summaries": """Summaries

/summary
Summarize recent monitored group messages.

/summary <chat_id>
Summarize recent messages for a specific tracked group/channel.

You can also type:
- help me summarize <pasted text>
- summarize for me <pasted text>
""",
    "notes": """Notes

/notes
Starts notes wizard (list/view/edit/clear).

/notes <id>
Show full reminder details and full notes for one reminder.

Add or edit notes with:
- /edit <id> notes:<text>
- /edit <id> notes:    (clear notes)
""",
    "files": """Files (Image, Doc, Audio)

Image:
- send an image and caption, or reply to image
- example: summarize this image

DOCX/PDF:
- example: summarize this document

Audio/Voice/MP4:
- example: summarize this audio
- MP4 is converted to WAV before STT (ffmpeg required)

Draft flow:
- confirm
- confirm 1,3
- edit <n> ...
- remove <n>
- cancel
""",
    "models": """Models

/models
/model
/model <name>
/model text <name>
/model vision <name>
/model tag <name> vision
/model untag <name> vision
/status
""",
    "sync": """Sync

/sync both
Run two-way sync with Google Calendar.

/sync import
Import Google Calendar events to Telegram reminders.

/sync export
Export Telegram reminders to Google Calendar.

Behavior:
- open reminders with due date -> pushed to calendar
- calendar events -> imported/updated as reminders
- all-day events stay date-only
- no-due reminders are not pushed
""",
    "examples": """Examples

/add Submit form link:https://example.com p:mid at:fri 5pm
/add Read article p:low at:none
/add Plan presentation topic:work at:next monday
/add Plan presentation #work next monday !high
/edit 12 at:none every:none
/edit 12 topic:work
/notes
/notes 12
/topics
/topics all
/topics create work
/topics rename 3 admin
/topics merge 4 2
/topic add 12 work
/topic remove 12 work
/summary -1002219388089
/sync both
""",
}


MESSAGES = {
    "usage_add": "Usage: /add <task> (guided) OR /add <task> [topic:<a,b>|#tag] [p:high|!h] [at:tomorrow 9am|none] [every:daily|weekly|monthly|@daily] [link:<url>]",
    "usage_done": "Usage: /done <id>",
    "usage_edit": "Usage: /edit <id> [title:<text>] [topic:<a,b>|topic:+a|topic:-a|topic:none] [p:<priority>] [at:<datetime>] [notes:<text>] [link:<url>] [every:daily|weekly|monthly|none]",
    "usage_delete": "Usage: /delete <id>",
    "usage_notes": "Usage: /notes OR /notes <id>",
    "usage_list": "Usage: /list all | /list <chat_id> | /list priority high | /list topic <name> | /list archived [topic <name>] | /list due 14d | /list today|tomorrow|overdue",
    "usage_summary": "Usage: /summary OR /summary <chat_id>",
    "usage_topics": "Usage: /topics | /topics all | /topics create <name> | /topics rename <id> <new> | /topics delete <id> | /topics merge <from> <to>",
    "usage_topics_create": "Usage: /topics create <name>",
    "usage_topics_rename": "Usage: /topics rename <id> <new name>",
    "usage_topics_delete": "Usage: /topics delete <id>",
    "usage_topics_merge": "Usage: /topics merge <from_id> <to_id>",
    "usage_topic": "Usage: /topic add <reminder_id> <name> OR /topic remove <reminder_id> <name>",
    "usage_sync": "Usage: /sync <both|import|export>",
    "usage_list_due": "Use day format like: /list due 14d",
    "usage_model_tag": "Usage: /model tag <name> vision OR /model untag <name> vision",
    "error_id_number": "Reminder id must be a number.",
    "error_topic_id_number": "Topic id must be a number.",
    "error_title_empty": "Title cannot be empty.",
    "error_due_empty": "Due date/time cannot be empty.",
    "error_recurrence_requires_due": "Recurrence requires a due date/time. Set at:<date> or every:none.",
    "error_edit_no_fields": "Please provide fields to update.",
    "error_list_unknown": "Unknown list filter. Try /list all, /list topic <name>, /list archived, /list due 14d, or /help",
    "error_list_invalid": "Invalid list value. Try /help",
    "error_list_empty": "No matching open reminders.",
    "error_list_archived_empty": "No matching archived reminders.",
    "error_topics_empty": "No topics found yet.",
    "error_topics_missing_create": "These topics do not exist yet: {topics}. Create first with /topics create <name>.",
    "status_topics_suggestions": "Did you mean: {topics}",
    "status_topic_created": "Created topic: {topic}",
    "status_topic_renamed": "Renamed topic #{id} to: {topic}",
    "status_topic_deleted": "Deleted topic #{id}.",
    "status_topics_merged": "Merged topic #{from_id} into #{to_id}.",
    "error_topic_not_found": "Topic #{id} not found.",
    "error_topics_merge_failed": "Merge failed. Check topic IDs and try again.",
    "status_topic_added_to_reminder": "Added topic '{topic}' to reminder #{id}.",
    "status_topic_removed_from_reminder": "Removed topic '{topic}' from reminder #{id}.",
    "error_topic_not_on_reminder": "Topic '{topic}' is not linked to reminder #{id}.",
    "error_topic_required": "At least one topic is required. Create one first with /topics create <name> and then use topic:<name>.",
    "error_summary_target": "No summary target configured. Set MONITORED_GROUP_CHAT_ID or pass /summary <chat_id>.",
    "status_sync_start": "Running {mode} sync with Google Calendar...",
    "status_sync_done": "Sync complete ({mode}). reminders->calendar: {push_ok}/{push_total}, calendar->reminders: +{pull_created}, updated: {pull_updated}.",
    "status_sync_failed_details": "Some reminders were not pushed: {details}",
    "error_sync_disabled": "Google Calendar sync is disabled. Set GCAL_SYNC_ENABLED=true and configure credentials.",
    "status_summary_start": "Got it - summarizing now...",
    "status_summary_done": "Summary complete. I will now draft reminder suggestions.",
    "status_text_summary_started": "Working on that summary now...",
    "error_text_summary_failed": "I hit an error while summarizing: {error}",
    "status_image_analyzing": "Got it - analyzing image now...",
    "status_image_analyzing_draft": "Got it - analyzing image and drafting reminders...",
    "status_doc_analyzing": "Got it - reading and summarizing this document now...",
    "status_doc_analyzing_draft": "Got it - reading this document and drafting reminders...",
    "status_audio_transcribe": "Got it - transcribing and summarizing this audio now...",
    "status_audio_transcribe_draft": "Got it - transcribing audio and drafting reminders...",
    "status_draft_analyzing": "Analyzing content and drafting reminders...",
    "status_draft_none": "Done. I found no reminders to suggest. {reason}",
    "status_draft_review": "Done. Please review the draft reminders below.",
    "error_draft_failed": "I hit an error while drafting reminders: {error}",
    "status_draft_saving": "Saving selected reminders...",
    "status_draft_saved": "Done. Saved reminders:",
    "error_draft_save_failed": "I hit an error while saving reminders: {error}",
    "status_draft_invalid": "Done, but I could not extract valid reminder drafts yet. You can still create one with /add or reply with exact details.",
    "error_models_empty": "No Ollama models found. Pull one with: ollama pull <model>",
    "error_model_not_installed": "Model not installed. Run: ollama pull {model}",
    "status_model_tagged": "Tagged as vision-capable: {model}",
    "status_model_untagged": "Removed vision tag: {model}",
    "usage_model_role": "Usage: /model {role} <name>",
    "usage_model_wizard": "Usage: /model (starts wizard) OR /model text <name> OR /model vision <name>",
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
    "error_delete_failed": "Failed to delete reminder: {error}",
    "error_update_failed": "Reminder #{id} could not be updated.",
    "error_notes_empty": "No open reminders with notes right now.",
    "error_notes_empty_for_id": "Reminder #{id} has no notes.",
    "error_summary_run": "I hit an error while running summary: {error}",
    "error_add_missing_title": "Missing reminder title. Example: /add Pay rent at:tomorrow 9am",
    "error_add_missing_due": "Missing or invalid date/time. Example: /add Pay rent tomorrow 9am !h #finance OR /add Read article at:none",
    "error_edit_invalid_due": "Invalid date/time in at:. Example: at:tomorrow 9am or at:none",
    "status_due_guess": "I interpreted the due date as {due_local} ({timezone}). Reply `yes` to confirm or reply with a clearer date/time.",
    "error_due_confirm_parse": "I still cannot parse that date/time. Reply with something like: tomorrow 9am, 21 Feb 14:30, or next Monday 10am.",
    "status_due_recheck": "Got it. I now interpret due date as {due_local} ({timezone}). Reply `yes` to confirm, or send another date/time.",
    "status_pending_add_cancelled": "Okay, discarded this pending reminder draft.",
    "error_text_need_due": "I detected reminder intent but need a date/time. Example: /add Pay rent at:tomorrow 9am or /add Read article at:none",
    "status_use_add_for_confirmation": "I parsed your date/time with medium confidence. Please use /add so I can confirm it before saving. Example: /add Pay rent p:high at:next Monday",
    "error_draft_invalid_at": "Invalid at: value. Example: at:tomorrow 9am or at:none",
}


def msg(key: str, **kwargs: object) -> str:
    template = MESSAGES[key]
    return template.format(**kwargs)
