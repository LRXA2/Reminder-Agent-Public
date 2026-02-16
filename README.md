# Reminder Agent

Personal Telegram assistant for reminders, summaries, and lightweight planning.

## Features

- Create reminders from Telegram chat input
- Priority levels: `immediate`, `high`, `mid`, `low`
- Scheduled notifications with recurring reminders (`daily`, `weekly`, `monthly`)
- Mark done and auto-archive reminders
- Auto-delete archived reminders after retention period
- Summarize monitored group messages with local Ollama
- Summarize pasted text in DM and convert to reminder with follow-up urgency/due date
- Create reminders from image replies using a vision-capable Ollama model
- Summarize DOCX/PDF attachments and create reminders from document content
- Transcribe audio/voice messages locally with faster-whisper for summary + reminder drafting
- Query reminder views: all, priority, date windows, overdue
- Manage text + vision Ollama models from Telegram (`/models`, `/model`, `/status`)

## Tech Stack

- Python 3.10+
- `python-telegram-bot`
- `APScheduler`
- `sqlite3` (built into Python)
- `dateparser`
- `pypdf`
- `faster-whisper`
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
MESSAGE_RETENTION_DAYS=14

OLLAMA_BASE_URL=http://localhost:11434
OLLAMA_MODEL=
OLLAMA_TEXT_MODEL=
OLLAMA_VISION_MODEL=
OLLAMA_AUTOSTART=true
OLLAMA_START_TIMEOUT_SECONDS=20
OLLAMA_USE_HIGHEST_VRAM_GPU=true

STT_PROVIDER=faster_whisper
STT_MODEL=large-v3
STT_DEVICE=auto
STT_COMPUTE_TYPE=auto
STT_USE_HIGHEST_VRAM_GPU=true

DIGEST_TIMES_LOCAL=23:00,3:30,12:00
```

Notes:

- `OLLAMA_MODEL` can be blank. Bot auto-picks first installed model.
- `OLLAMA_TEXT_MODEL` overrides `OLLAMA_MODEL` for text tasks when set.
- `OLLAMA_VISION_MODEL` controls image understanding; falls back to active text model if blank.
- `DIGEST_TIMES_LOCAL` follows `DEFAULT_TIMEZONE`.
- Legacy UTC digest env names are still read as fallback (`DIGEST_TIMES_UTC`, `DIGEST_HOUR_UTC`, `DIGEST_MINUTE_UTC`).
- If you are DM-only for now, keep `MONITORED_GROUP_CHAT_ID=0`.
- `MESSAGE_RETENTION_DAYS` controls how long stored chat messages are kept before auto-deletion.
- `STT_PROVIDER=faster_whisper` enables local transcription for audio/voice attachments.
- `STT_MODEL=large-v3` is accuracy-first and can be heavy on CPU.
- `STT_USE_HIGHEST_VRAM_GPU=true` selects the Nvidia GPU with the largest VRAM first.

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

## Test Speech-to-Text (Without Bot)

Use the local STT test script first:

```bash
python scripts/test_stt.py
```

It will prompt for audio file path. You can also pass it directly:

```bash
python scripts/test_stt.py --file "D:\Aarron\Recording.m4a" --device cuda --compute-type float16
```

Useful options:

- `--highest-vram-gpu` picks the Nvidia GPU with largest VRAM
- `--device cpu --compute-type int8` forces CPU fallback

DB file is created automatically on first run (`DB_PATH`, default `reminder_agent.db`).

## Commands

- `/help`
- `/add Pay rent p:high at:tomorrow 9am`
- `/add Standup prep p:mid at:8am every:daily`
- `/done 12`
- `/edit 12 p:high at:tomorrow 9am`
- `/edit 12 title:Review ASAVC(M) details notes:Bring Smart No.4 every:none`
- `/delete 12`
- `/detail 12`
- `/list all`
- `/list priority high`
- `/list due 14d`
- `/list today`
- `/list tomorrow`
- `/list overdue`
- `/summary`
- `/models`
- `/model`
- `/model mistral-small3.2:24b` (set text model)
- `/model text mistral-small3.2:24b`
- `/model vision mistral-small3.2:24b`
- `/model tag mistral-small3.2:24b vision`
- `/model untag mistral-small3.2:24b vision`
- `/status`

Image reminder flow in DM:

- Send an image with caption like `remind me high tomorrow 9am`, or
- Reply to an image with something like `remind me high tomorrow 9am`
- You can also ask `summarize this image` (caption or reply), with optional reminder details
- Attachment routing is now generic (image/docx/audio), with full AI extraction currently enabled for images
- For DOCX/PDF, use captions or replies like `summarize this document` or `create reminder low tomorrow 9am`
- For audio/voice, use captions or replies like `summarize this audio` or `create reminders from this recording`

Natural-language shortcuts in DM:

- `help me summarize <pasted text>`
- `summarize for me <pasted text>`
- `add as reminder high tomorrow 9am` (as a reply to an existing message)
- `set to tomorrow 8am high` (as a reply to a reminder card that contains `ID:`)
- `what hackathons are available on 1 Mar - 15 Mar`

For summary/image/document flows, reminder creation is now draft-first:

- Bot proposes one or more reminder drafts
- You review/edit first
- Reply with `confirm`, `confirm 1,3`, `edit <n> ...`, `remove <n>`, or `cancel`

Reminder creation response format is standardized as:

- `ID: <id>`
- `Title: <title>`
- `Date: dd/mm/yy [HH:mm]`

Use `/detail <id>` to view full reminder notes/details.

## How Reminder Flow Works

- Inbound messages are stored selectively (monitored group only, plus personal chat messages that look hackathon-related).
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
- STT GPU error `cublas64_12.dll is not found`: install CUDA 12.x (not only 11.x/13.x). The app/test script already tries to register CUDA 12 DLL dirs automatically on Windows.
- To verify CUDA DLL manually on Windows:

```bash
python -c "import os,ctypes; os.add_dll_directory(r'C:\Program Files\NVIDIA GPU Computing Toolkit\CUDA\v12.8\bin'); ctypes.CDLL('cublas64_12.dll'); print('cublas OK')"
```
