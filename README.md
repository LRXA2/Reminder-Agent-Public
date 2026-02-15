# Reminder Agent

Personal Telegram assistant for reminders, summaries, and lightweight planning.

Current status: active prototype, with upcoming integrations in progress.

## Features

- Create reminders from Telegram chat input
- Priority levels: `immediate`, `high`, `mid`, `low`
- Scheduled notifications with recurring reminders (`daily`, `weekly`, `monthly`)
- Mark done and auto-archive reminders
- Auto-delete archived reminders after retention period
- Summarize monitored group messages with local Ollama
- Summarize pasted text in DM and convert to reminder with follow-up urgency/due date
- Query reminder views: all, priority, date windows, overdue
- Manage Ollama model from Telegram (`/models`, `/model`, `/status`)

## Tech Stack

- Python 3.10+
- `python-telegram-bot`
- `APScheduler`
- `sqlite3` (built into Python)
- `dateparser`
- Ollama (local LLM)

## Project Structure

```text
main.py
src/
  app/
    reminder_bot.py
  clients/
    ollama_client.py
  core/
    config.py
  storage/
    database.py
```

## Setup

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

Create env file:

```bash
copy .env.example ..\.env
```

By default the app loads env from one folder above repo root (`..\.env`).
Optional override: set `ENV_FILE` to a full env path.

## Environment Variables

Example (`..\.env`):

```env
TELEGRAM_BOT_TOKEN=YOUR_REAL_BOT_TOKEN
PERSONAL_CHAT_ID=YOUR_REAL_CHAT_ID
MONITORED_GROUP_CHAT_ID=0
DB_PATH=reminder_agent.db
DEFAULT_TIMEZONE=Asia/Singapore
ARCHIVE_RETENTION_DAYS=30

OLLAMA_BASE_URL=http://localhost:11434
OLLAMA_MODEL=
OLLAMA_AUTOSTART=true
OLLAMA_START_TIMEOUT_SECONDS=20
OLLAMA_USE_HIGHEST_VRAM_GPU=true

DIGEST_TIMES_UTC=23:00,3:30,12:00
```

Notes:

- `OLLAMA_MODEL` can be blank. Bot auto-picks first installed model.
- `DIGEST_TIMES_UTC` uses UTC times.
- If you are DM-only for now, keep `MONITORED_GROUP_CHAT_ID=0`.

## Ollama Setup

Install Ollama, then pull at least one model:

```bash
ollama pull llama3.1:8b
```

You can run `ollama serve` manually, or let bot autostart it (`OLLAMA_AUTOSTART=true`).

## Run

```bash
python main.py
```

DB file is created automatically on first run (`DB_PATH`, default `reminder_agent.db`).

## Commands

- `/help`
- `/add Pay rent p:high at:tomorrow 9am`
- `/add Standup prep p:mid at:8am every:daily`
- `/done 12`
- `/list all`
- `/list priority high`
- `/list due 14d`
- `/list today`
- `/list tomorrow`
- `/list overdue`
- `/summary`
- `/models`
- `/model`
- `/model mistral-small3.2:24b`
- `/status`

Natural-language shortcuts in DM:

- `help me summarize <pasted text>`
- `summarize for me <pasted text>`
- `what hackathons are available on 1 Mar - 15 Mar`

## How Reminder Flow Works

- Inbound Telegram messages are stored in SQLite.
- Router decides command vs intent.
- Reminder parser extracts task, priority, and datetime.
- Scheduler checks due reminders every 30 seconds and sends notifications.
- `/done <id>` archives reminder immediately.
- Archive cleanup job removes old archived items daily.

## Troubleshooting

- `Missing TELEGRAM_BOT_TOKEN`: add it to `..\.env` and restart.
- `ModuleNotFoundError`: run `python -m pip install -r requirements.txt`.
- `ollama serve ... address already in use`: Ollama is already running, continue.
- No group summaries: ensure bot has access and set `MONITORED_GROUP_CHAT_ID`.

## In Progress

- Google Calendar integration (OAuth + event sync into reminder timeline)
- Image input summarization (OCR pipeline for posters/screenshots)
- Audio input summarization (speech-to-text transcription before summarization)
