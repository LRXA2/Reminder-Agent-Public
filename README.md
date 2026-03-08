# Reminder Agent

A personal Telegram bot for reminders, summaries, and lightweight planning.

## What it does

- Create reminders from chat text (`/add`, natural language, reply flows)
- Manage reminders (`/edit`, `/done`, `/delete`, `/list`)
- Organize by topics (`/topics`, `/topic`)
- Draft reminders from summaries/images/docs/audio before saving
- Optional Google Calendar sync (`/sync import|export|both`)

## Quick start

1) Create and activate a virtual environment

```bash
python -m venv .venv
.venv\Scripts\activate
```

2) Install dependencies

```bash
pip install -r requirements.txt
```

3) Create your env file

```bash
copy .env.example ..\.env
```

4) Set required values in `..\.env`

- `TELEGRAM_BOT_TOKEN`
- `PERSONAL_CHAT_ID`
- optional: `ALLOWED_TELEGRAM_USER_IDS`

5) Run the bot

```bash
python main.py
```

## Basic usage

- Add reminder:

```text
/add Pay rent p:high at:tomorrow 9am
```

- Edit reminder:

```text
/edit 12 p:mid at:fri 5pm
```

- Complete reminder:

```text
/done 12
```

- List reminders:

```text
/list all
/list today
/list overdue
```

- Topics:

```text
/topics
/topics create work
/topic add 12 work
```

- Notes:

```text
/notes
/notes 12
```

## Attachment workflows

In your personal chat, send or reply with:

- image + caption: `summarize this image`
- doc/pdf + caption: `summarize this document`
- audio/voice + caption: `create reminders from this recording`

The bot proposes drafts first, then you confirm:

- `confirm`
- `confirm 1,3`
- `edit <n> ...`
- `remove <n>`
- `cancel`

## Gmail filter tuning (optional)

`GMAIL_ACCOUNTS_JSON` supports lightweight per-account filter tuning fields:

- `risky_tlds`
- `shortener_domains`
- `suspicious_phrases`
- `promotional_phrases`
- `urgent_subject_phrases`

You can also keep those keys in a separate JSON file and point to it with `filter_keys_file` in each account entry.

Example account entry:

```json
{
  "account_id": "work",
  "credentials_file": "secrets/work_credentials.json",
  "token_file": "secrets/work_token.json",
  "query": "label:inbox is:unread newer_than:2d",
  "sender_allowlist": ["boss@company.com"],
  "sender_trusted_domains": ["company.com"],
  "filter_keys_file": "D:/secrets/reminder_agent/work_filter_keys.json",
  "shortener_domains": ["bit.ly", "tinyurl.com"],
  "suspicious_phrases": ["verify your account", "urgent action required"],
  "promotional_phrases": ["unsubscribe", "newsletter"],
  "urgent_subject_phrases": ["urgent", "action required"],
  "risky_tlds": ["zip", "click", "xyz"],
  "telegram_chat_id": 123456789
}
```

If `filter_keys_file` is set, values from that file override the same inline keys for that account.

## Tests

Run all tests:

```bash
python run_tests.py
```

## Optional quality checks

```bash
ruff check src tests
pyright
```

## Notes

- Default env path is `..\.env` (one folder above repo)
- Database is local SQLite (`reminder_agent.db` by default)
- If using audio from MP4, ensure `ffmpeg` is available in PATH
