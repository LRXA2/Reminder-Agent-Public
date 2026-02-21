# Reminder Agent Architecture

## Runtime Entry Points

- `main.py` boots config, database, integrations, and starts polling.
- `src/app/reminder_bot.py` is the runtime facade/orchestrator:
  - wires clients/services/handlers
  - registers Telegram command/message handlers
  - delegates chat pipeline and background jobs to specialized handlers

## Telegram Surface

Commands registered in `src/app/reminder_bot.py`:

- `/help`
- `/add`, `/edit`, `/done`, `/delete`
- `/notes` (`/note` alias)
- `/list`
- `/topics`, `/topic`
- `/summary`
- `/sync`
- `/models`, `/model`
- `/status`

Non-command text is handled by the chat pipeline (`src/app/handlers/chat_pipeline.py`) and routed to:

- `TextInputHandler` for text intents and summarize flows
- `AttachmentInputHandler` for image/doc/audio/video reply flows
- `ReminderDraftManager` for pending draft follow-up (`confirm`, `edit`, `remove`, etc.)

## Core Modules

- `src/app/reminder_bot.py`
  - Telegram handler registration
  - command entrypoints and delegations
  - shared parsing/sync/date helpers used across handlers

- `src/app/handlers/chat_pipeline.py`
  - normal message pipeline
  - pending workflow chain order
  - caption-based attachment entry

- `src/app/handlers/job_runner.py`
  - scheduled jobs: due reminders, cleanup, auto summaries, daily digest
  - group-summary build/summarize helpers for scheduled flows

- `src/app/handlers/wizards/`
  - `handler.py`: UI wizard facade
  - `ui_router.py`: callback routing (`ui:*`)
  - `edit_wizard.py`, `notes_wizard.py`, `topics_wizard.py`, `delete_wizard.py`
  - `keyboards.py`, `common.py`

- `src/app/handlers/text_input/handler.py`
  - text-input facade
  - split modules:
    - `router.py` (intent routing)
    - `summary_handler.py`
    - `reply_workflow_handler.py`
    - `reminder_text_handler.py`
    - `common.py`

- `src/app/handlers/attachment_input/handler.py`
  - attachment-input facade
  - split modules:
    - `router.py` (attachment type detection + dispatch)
    - `audio_input_handler.py`
    - `visual_input_handler.py`
    - `document_input_handler.py`
    - `models.py`

- `src/app/handlers/reminder_draft/manager.py`
  - draft proposal extraction/orchestration
  - delegates to `reminder_draft/session_handler.py` for interactive follow-up
  - delegates normalization/date/refinement logic via:
    - `reminder_draft/schema_mixin.py`
    - `reminder_draft/datetime_mixin.py`
    - `reminder_draft/refinement_mixin.py`

- `src/app/handlers/add_edit_handler.py`
  - `/add` and `/edit` command flows
  - add wizard progression

- `src/app/handlers/topics_notes_handler.py`
  - `/topics`, `/topic`, `/notes` command flows

- `src/app/handlers/list_sync_model_handler.py`
  - `/list`, `/sync`, `/models`, `/model` command flows
  - list/sync/model pending wizard handling

- `src/app/handlers/summary_status_handler.py`
  - `/summary`, `/status` command flows

- `src/app/handlers/flow_state_service.py`
  - centralized pending-flow state resets

- `src/app/handlers/datetime_parser.py`
  - deterministic date parsing pipeline used by add/edit/draft flows
  - precedence: explicit date -> relative phrase -> direct parse -> search fallback

- `src/storage/database.py`
  - SQLite schema + migrations
  - reminders, messages, summaries, topics, calendar mappings, tombstones

- `src/integrations/google_calendar_service.py`
  - Google Calendar event upsert/delete/list
  - event body mapping for timed/all-day reminders

- `src/clients/ollama_client.py`
  - text/vision generation wrappers

- `src/clients/stt_client.py`
  - local speech-to-text wrapper

## Data Model Highlights

- `reminders`
  - core reminder records
  - scoped by `chat_id_to_notify`

- `topics` + `reminder_topics`
  - topic index and many-to-many reminder-topic links

- `calendar_sync`
  - reminder-to-calendar event mapping

- `calendar_sync_tombstones`
  - protects deleted events from re-import resurrection

- `messages` / `summaries`
  - ingest and summarization support tables

## Sync Design

- Export: open reminders with due date -> calendar events
- Import: calendar events -> reminders
- Tombstones block re-import of recently deleted external event IDs
- Command modes:
  - `/sync both`
  - `/sync import`
  - `/sync export`

## Date Parsing Strategy

- Shared parser: `datetime_parser.py`
- Confidence tags: `high|medium|low`
- All-day handling for date-without-time
- LLM fallback remains available where deterministic parse fails
- Optional debug logging controlled by `DATETIME_PARSE_DEBUG`

## Testing

Current focused test coverage includes:

- `tests/test_database_scoping.py`
- `tests/test_topics.py`
- `tests/test_calendar_tombstones.py`
- `tests/test_datetime_parser.py`
- `tests/test_draft_schema.py`
- `tests/test_wizard_callbacks.py`
- `tests/test_attachment_input_modules.py`

Run:

```bash
python -m compileall src tests
python run_tests.py
```
