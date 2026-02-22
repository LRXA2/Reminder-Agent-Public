# Reminder Agent Architecture

## Runtime Entry Points

- `main.py` boots config, database, integrations, and starts polling.
- `src/app/bot_orchestrator.py` is the runtime facade/orchestrator:
  - wires clients/services/handlers
  - registers Telegram command/message handlers
  - delegates chat pipeline and background jobs to specialized handlers

## Telegram Surface

Commands registered in `src/app/bot_orchestrator.py`:

- `/help`
- `/add`, `/edit`, `/done`, `/delete`
- `/notes` (`/note` alias)
- `/list`
- `/topics`, `/topic`
- `/summary`
- `/sync`
- `/models`, `/model`
- `/status`

Non-command text is handled by the chat pipeline (`src/app/handlers/runtime/message_pipeline.py`) and routed to:

- `TextInputHandler` for text intents and summarize flows
- `AttachmentInputHandler` for image/doc/audio/video reply flows
- `ReminderDraftManager` for pending draft follow-up (`confirm`, `edit`, `remove`, etc.)

## Core Modules

- `src/app/bot_orchestrator.py`
  - Telegram handler registration
  - command/callback/job wiring and service composition

- `src/app/handlers/runtime/message_ingest_handler.py`
  - inbound message persistence (`group=-1` handler)
  - ingest filtering policy for monitored groups and DM hackathon signals

- `src/app/handlers/commands/completion_delete_handler.py`
  - `/done` and `/delete` command flows
  - delete wizard entrypoint and direct delete-by-id handling

- `src/app/handlers/services/reminder_rules.py`
  - shared reminder-domain utility logic
  - topic splitting, missing-topic message formatting
  - inline `/add` payload heuristic
  - notes-list candidate heuristic
  - recurrence next-due calculation

- `src/app/handlers/services/calendar_sync_handler.py`
  - Google Calendar import/export orchestration
  - calendar event -> due datetime conversion helpers
  - import note cleanup and link extraction helpers
  - calendar upsert/delete hooks used by reminder create/update/delete flows

- `src/app/handlers/runtime/message_pipeline.py`
  - normal message pipeline
  - pending workflow chain order
  - caption-based attachment entry

- `src/app/handlers/services/scheduler_jobs.py`
  - scheduled jobs: due reminders, cleanup, auto summaries, daily digest
  - group-summary build/summarize helpers for scheduled flows
  - recurring reminder roll-forward via `ReminderLogicHandler`

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
    - `attachment_types.py`

- `src/app/handlers/reminder_draft/manager.py`
  - draft proposal extraction/orchestration
  - delegates to `reminder_draft/session_handler.py` for interactive follow-up
  - delegates normalization/date/refinement logic via:
    - `reminder_draft/schema_mixin.py`
    - `reminder_draft/datetime_mixin.py`
    - `reminder_draft/refinement_mixin.py`

- `src/app/handlers/commands/add_edit/handler.py`
  - `/add` and `/edit` command flows
  - add wizard progression
  - delegates to:
    - `commands/add_edit/parsing.py`
    - `commands/add_edit/commands_flow.py`
    - `commands/add_edit/confirmation_workflow.py`
    - `commands/add_edit/wizard_workflow.py`

- `src/app/handlers/commands/topics_notes_commands.py`
  - `/topics`, `/topic`, `/notes` command flows

- `src/app/handlers/commands/list_sync_models_handler.py`
  - `/list`, `/sync`, `/models`, `/model` command flows
  - list/sync/model pending wizard handling

- `src/app/handlers/commands/summary_status_handler.py`
  - `/summary`, `/status` command flows

- `src/app/handlers/runtime/flow_state_service.py`
  - centralized pending-flow state resets

- `src/app/handlers/services/datetime_resolution_handler.py`
  - natural-language datetime resolution orchestration
  - LLM fallback parsing for low-confidence date inputs
  - all-day normalization, explicit-time detection, JSON extraction helpers

- `src/app/handlers/datetime_parser.py`
  - deterministic date parsing pipeline used by add/edit/draft flows
  - precedence: explicit date -> relative phrase -> direct parse -> search fallback

- `src/storage/database.py`
  - SQLite schema + migrations
  - reminders, messages, summaries, topics, calendar mappings, tombstones

- `src/integrations/google_calendar_service.py`
  - low-level Google Calendar API adapter
  - event upsert/delete/list and event body mapping

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
- Handler split:
  - `ListSyncModelHandler` owns `/sync` command UX + mode routing
  - `CalendarSyncHandler` owns import/export execution details
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
- `tests/test_add_edit_payload_parser.py`
- `tests/test_add_confirmation_workflow.py`
- `tests/test_message_ingest_handler.py`
- `tests/test_reminder_rules.py`
- `tests/test_message_pipeline.py`
- `tests/test_scheduler_jobs.py`
- `tests/test_list_sync_models_handler.py`

Run:

```bash
python -m compileall src tests
python run_tests.py
```
