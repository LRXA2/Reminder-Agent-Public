import os
from dataclasses import dataclass
from pathlib import Path
from typing import Tuple

from dotenv import load_dotenv


def _load_env() -> None:
    env_file = os.getenv("ENV_FILE", "").strip()
    if env_file:
        load_dotenv(dotenv_path=env_file)
        return

    repo_root = Path(__file__).resolve().parents[2]
    parent_root = repo_root.parent
    candidates = [
        parent_root / ".env",
        repo_root / ".env",
        Path.cwd() / ".env",
    ]

    for candidate in candidates:
        if candidate.exists():
            load_dotenv(dotenv_path=candidate)
            return

    load_dotenv()


_load_env()


@dataclass(frozen=True)
class Settings:
    telegram_bot_token: str
    personal_chat_id: int
    monitored_group_chat_id: int
    db_path: str
    default_timezone: str
    archive_retention_days: int
    message_retention_days: int
    ollama_base_url: str
    ollama_model: str
    ollama_text_model: str
    ollama_vision_model: str
    ollama_autostart: bool
    ollama_start_timeout_seconds: int
    ollama_use_highest_vram_gpu: bool
    stt_provider: str
    stt_model: str
    stt_device: str
    stt_compute_type: str
    stt_use_highest_vram_gpu: bool
    digest_hour_local: int
    digest_minute_local: int
    digest_times_local: Tuple[tuple[int, int], ...]


def _int_env(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None or value.strip() == "":
        return default
    return int(value)


def _bool_env(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    return default


def _parse_digest_times(value: str) -> Tuple[tuple[int, int], ...]:
    times: list[tuple[int, int]] = []
    for raw_part in value.split(","):
        part = raw_part.strip()
        if not part:
            continue
        if ":" in part:
            hour_text, minute_text = part.split(":", 1)
            hour = int(hour_text.strip())
            minute = int(minute_text.strip())
        else:
            hour = int(part)
            minute = 0
        if hour < 0 or hour > 23 or minute < 0 or minute > 59:
            raise ValueError(f"Invalid DIGEST_TIMES_LOCAL time: {part}")
        times.append((hour, minute))
    return tuple(times)


def get_settings() -> Settings:
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        raise ValueError("Missing TELEGRAM_BOT_TOKEN in environment.")

    digest_times_raw = os.getenv("DIGEST_TIMES_LOCAL", "").strip()
    if not digest_times_raw:
        digest_times_raw = os.getenv("DIGEST_TIMES_UTC", "").strip()
    digest_times = _parse_digest_times(digest_times_raw) if digest_times_raw else tuple()

    return Settings(
        telegram_bot_token=token,
        personal_chat_id=_int_env("PERSONAL_CHAT_ID", 0),
        monitored_group_chat_id=_int_env("MONITORED_GROUP_CHAT_ID", 0),
        db_path=os.getenv("DB_PATH", "reminder_agent.db"),
        default_timezone=os.getenv("DEFAULT_TIMEZONE", "UTC"),
        archive_retention_days=_int_env("ARCHIVE_RETENTION_DAYS", 30),
        message_retention_days=max(0, _int_env("MESSAGE_RETENTION_DAYS", 14)),
        ollama_base_url=os.getenv("OLLAMA_BASE_URL", "http://localhost:11434"),
        ollama_model=os.getenv("OLLAMA_MODEL", "").strip(),
        ollama_text_model=os.getenv("OLLAMA_TEXT_MODEL", "").strip(),
        ollama_vision_model=os.getenv("OLLAMA_VISION_MODEL", "").strip(),
        ollama_autostart=_bool_env("OLLAMA_AUTOSTART", True),
        ollama_start_timeout_seconds=max(3, _int_env("OLLAMA_START_TIMEOUT_SECONDS", 20)),
        ollama_use_highest_vram_gpu=_bool_env("OLLAMA_USE_HIGHEST_VRAM_GPU", True),
        stt_provider=os.getenv("STT_PROVIDER", "faster_whisper").strip().lower(),
        stt_model=os.getenv("STT_MODEL", "large-v3").strip(),
        stt_device=os.getenv("STT_DEVICE", "auto").strip().lower(),
        stt_compute_type=os.getenv("STT_COMPUTE_TYPE", "auto").strip().lower(),
        stt_use_highest_vram_gpu=_bool_env("STT_USE_HIGHEST_VRAM_GPU", True),
        digest_hour_local=max(0, min(23, _int_env("DIGEST_HOUR_LOCAL", _int_env("DIGEST_HOUR_UTC", 9)))),
        digest_minute_local=max(0, min(59, _int_env("DIGEST_MINUTE_LOCAL", _int_env("DIGEST_MINUTE_UTC", 0)))),
        digest_times_local=digest_times,
    )
