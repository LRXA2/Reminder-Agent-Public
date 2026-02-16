import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable


PRIORITY_RANK = {
    "immediate": 4,
    "high": 3,
    "mid": 2,
    "low": 1,
}


class Database:
    def __init__(self, db_path: str):
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL;")
            self._conn.execute("PRAGMA foreign_keys=ON;")
        self._init_schema()

    def _init_schema(self) -> None:
        statements = [
            "DROP TABLE IF EXISTS jobs;",
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                telegram_user_id TEXT UNIQUE NOT NULL,
                username TEXT,
                timezone TEXT DEFAULT 'UTC',
                created_at_utc TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            """,
            """
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id TEXT NOT NULL,
                telegram_message_id TEXT NOT NULL,
                sender_telegram_id TEXT,
                text TEXT,
                chat_type TEXT,
                source_type TEXT NOT NULL,
                direction TEXT NOT NULL,
                received_at_utc TEXT NOT NULL,
                UNIQUE(chat_id, telegram_message_id)
            );
            """,
            """
            CREATE TABLE IF NOT EXISTS reminders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                source_message_id INTEGER,
                source_kind TEXT NOT NULL,
                title TEXT NOT NULL,
                notes TEXT,
                status TEXT NOT NULL DEFAULT 'open',
                priority TEXT NOT NULL DEFAULT 'mid',
                due_at_utc TEXT NOT NULL,
                timezone TEXT NOT NULL,
                recurrence_rule TEXT,
                chat_id_to_notify TEXT NOT NULL,
                created_at_utc TEXT NOT NULL,
                updated_at_utc TEXT NOT NULL,
                done_at_utc TEXT,
                archived_at_utc TEXT,
                last_notified_at_utc TEXT,
                last_notified_for_due_at_utc TEXT,
                FOREIGN KEY(user_id) REFERENCES users(id),
                FOREIGN KEY(source_message_id) REFERENCES messages(id)
            );
            """,
            """
            CREATE TABLE IF NOT EXISTS summaries (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                group_chat_id TEXT NOT NULL,
                window_start_utc TEXT NOT NULL,
                window_end_utc TEXT NOT NULL,
                summary_text TEXT NOT NULL,
                created_at_utc TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            """,
            """
            CREATE TABLE IF NOT EXISTS app_settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at_utc TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            """,
            "CREATE INDEX IF NOT EXISTS idx_messages_chat_received ON messages(chat_id, received_at_utc);",
            "CREATE INDEX IF NOT EXISTS idx_reminders_status_due ON reminders(status, due_at_utc);",
            "CREATE INDEX IF NOT EXISTS idx_reminders_status_priority_due ON reminders(status, priority, due_at_utc);",
        ]
        with self._lock:
            for stmt in statements:
                self._conn.execute(stmt)
            self._conn.commit()

    def _execute(self, query: str, params: Iterable[Any] = ()) -> sqlite3.Cursor:
        with self._lock:
            cursor = self._conn.execute(query, tuple(params))
            self._conn.commit()
            return cursor

    def upsert_user(self, telegram_user_id: int, username: str | None, timezone_name: str) -> int:
        now = datetime.now(timezone.utc).isoformat()
        self._execute(
            """
            INSERT INTO users(telegram_user_id, username, timezone, created_at_utc)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(telegram_user_id) DO UPDATE SET username=excluded.username
            """,
            (str(telegram_user_id), username, timezone_name, now),
        )
        row = self._execute("SELECT id FROM users WHERE telegram_user_id = ?", (str(telegram_user_id),)).fetchone()
        return int(row["id"])

    def save_inbound_message(
        self,
        chat_id: int,
        telegram_message_id: int,
        sender_telegram_id: int | None,
        text: str,
        chat_type: str,
        source_type: str,
        received_at_utc: str,
    ) -> int | None:
        try:
            cursor = self._execute(
                """
                INSERT INTO messages(
                    chat_id,
                    telegram_message_id,
                    sender_telegram_id,
                    text,
                    chat_type,
                    source_type,
                    direction,
                    received_at_utc
                ) VALUES (?, ?, ?, ?, ?, ?, 'inbound', ?)
                """,
                (
                    str(chat_id),
                    str(telegram_message_id),
                    str(sender_telegram_id) if sender_telegram_id else None,
                    text,
                    chat_type,
                    source_type,
                    received_at_utc,
                ),
            )
            return int(cursor.lastrowid)
        except sqlite3.IntegrityError:
            row = self._execute(
                "SELECT id FROM messages WHERE chat_id = ? AND telegram_message_id = ?",
                (str(chat_id), str(telegram_message_id)),
            ).fetchone()
            if row is None:
                return None
            return int(row["id"])

    def create_reminder(
        self,
        user_id: int,
        source_message_id: int | None,
        source_kind: str,
        title: str,
        notes: str,
        priority: str,
        due_at_utc: str,
        timezone_name: str,
        chat_id_to_notify: int,
        recurrence_rule: str | None,
    ) -> int:
        now = datetime.now(timezone.utc).isoformat()
        priority = priority if priority in PRIORITY_RANK else "mid"
        cursor = self._execute(
            """
            INSERT INTO reminders(
                user_id,
                source_message_id,
                source_kind,
                title,
                notes,
                status,
                priority,
                due_at_utc,
                timezone,
                recurrence_rule,
                chat_id_to_notify,
                created_at_utc,
                updated_at_utc
            ) VALUES (?, ?, ?, ?, ?, 'open', ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                user_id,
                source_message_id,
                source_kind,
                title,
                notes,
                priority,
                due_at_utc,
                timezone_name,
                recurrence_rule,
                str(chat_id_to_notify),
                now,
                now,
            ),
        )
        return int(cursor.lastrowid)

    def mark_done_and_archive(self, reminder_id: int) -> bool:
        now = datetime.now(timezone.utc).isoformat()
        cursor = self._execute(
            """
            UPDATE reminders
            SET status='archived',
                done_at_utc=?,
                archived_at_utc=?,
                updated_at_utc=?
            WHERE id=? AND status IN ('open', 'done')
            """,
            (now, now, now, reminder_id),
        )
        return cursor.rowcount > 0

    def delete_reminder_permanently(self, reminder_id: int) -> bool:
        cursor = self._execute("DELETE FROM reminders WHERE id = ?", (reminder_id,))
        return cursor.rowcount > 0

    def get_reminder_by_id(self, reminder_id: int) -> sqlite3.Row | None:
        with self._lock:
            row = self._conn.execute(
                """
                SELECT id, title, notes, priority, due_at_utc, status, source_kind, recurrence_rule, created_at_utc, updated_at_utc
                FROM reminders
                WHERE id = ?
                """,
                (reminder_id,),
            ).fetchone()
        return row

    def update_reminder_fields(
        self,
        reminder_id: int,
        title: str,
        notes: str,
        priority: str,
        due_at_utc: str,
        recurrence_rule: str | None,
    ) -> bool:
        now = datetime.now(timezone.utc).isoformat()
        normalized_priority = priority if priority in PRIORITY_RANK else "mid"
        cursor = self._execute(
            """
            UPDATE reminders
            SET title = ?,
                notes = ?,
                priority = ?,
                due_at_utc = ?,
                recurrence_rule = ?,
                updated_at_utc = ?
            WHERE id = ?
            """,
            (title, notes, normalized_priority, due_at_utc, recurrence_rule, now, reminder_id),
        )
        return cursor.rowcount > 0

    def list_reminders(self, mode: str, value: str | None = None) -> list[sqlite3.Row]:
        base = """
            SELECT id, title, priority, due_at_utc, status
            FROM reminders
            WHERE status='open'
        """
        params: list[Any] = []
        now = datetime.now(timezone.utc)

        if mode == "priority" and value:
            base += " AND priority = ?"
            params.append(value)
        elif mode == "today":
            start = now.replace(hour=0, minute=0, second=0, microsecond=0)
            end = start + timedelta(days=1)
            base += " AND due_at_utc >= ? AND due_at_utc < ?"
            params.extend([start.isoformat(), end.isoformat()])
        elif mode == "tomorrow":
            start = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
            end = start + timedelta(days=1)
            base += " AND due_at_utc >= ? AND due_at_utc < ?"
            params.extend([start.isoformat(), end.isoformat()])
        elif mode == "overdue":
            base += " AND due_at_utc < ?"
            params.append(now.isoformat())
        elif mode == "due_days" and value:
            days = int(value)
            end = now + timedelta(days=days)
            base += " AND due_at_utc >= ? AND due_at_utc <= ?"
            params.extend([now.isoformat(), end.isoformat()])

        base += " ORDER BY CASE priority WHEN 'immediate' THEN 4 WHEN 'high' THEN 3 WHEN 'mid' THEN 2 ELSE 1 END DESC, due_at_utc ASC"

        with self._lock:
            return list(self._conn.execute(base, tuple(params)).fetchall())

    def get_due_reminders(self, now_utc_iso: str) -> list[sqlite3.Row]:
        query = """
            SELECT *
            FROM reminders
            WHERE status='open'
              AND due_at_utc <= ?
              AND (last_notified_for_due_at_utc IS NULL OR last_notified_for_due_at_utc != due_at_utc)
            ORDER BY due_at_utc ASC
        """
        with self._lock:
            return list(self._conn.execute(query, (now_utc_iso,)).fetchall())

    def mark_reminder_notified(self, reminder_id: int, due_at_utc: str) -> None:
        now = datetime.now(timezone.utc).isoformat()
        self._execute(
            """
            UPDATE reminders
            SET last_notified_at_utc=?, last_notified_for_due_at_utc=?, updated_at_utc=?
            WHERE id=?
            """,
            (now, due_at_utc, now, reminder_id),
        )

    def update_recurring_due(self, reminder_id: int, next_due_at_utc: str) -> None:
        now = datetime.now(timezone.utc).isoformat()
        self._execute(
            """
            UPDATE reminders
            SET due_at_utc=?, updated_at_utc=?
            WHERE id=?
            """,
            (next_due_at_utc, now, reminder_id),
        )

    def delete_old_archived(self, retention_days: int) -> int:
        cutoff = datetime.now(timezone.utc) - timedelta(days=retention_days)
        cursor = self._execute(
            "DELETE FROM reminders WHERE status='archived' AND archived_at_utc IS NOT NULL AND archived_at_utc < ?",
            (cutoff.isoformat(),),
        )
        return int(cursor.rowcount)

    def delete_old_messages(self, retention_days: int) -> int:
        cutoff = datetime.now(timezone.utc) - timedelta(days=retention_days)
        cursor = self._execute(
            "DELETE FROM messages WHERE received_at_utc < ?",
            (cutoff.isoformat(),),
        )
        return int(cursor.rowcount)

    def fetch_recent_group_messages(self, group_chat_id: int, limit: int = 50) -> list[sqlite3.Row]:
        query = """
            SELECT text, sender_telegram_id, received_at_utc
            FROM messages
            WHERE chat_id = ? AND source_type='group' AND direction='inbound'
            ORDER BY received_at_utc DESC
            LIMIT ?
        """
        with self._lock:
            return list(self._conn.execute(query, (str(group_chat_id), limit)).fetchall())

    def fetch_recent_chat_messages(self, chat_id: int, limit: int = 200) -> list[sqlite3.Row]:
        query = """
            SELECT text, sender_telegram_id, received_at_utc
            FROM messages
            WHERE chat_id = ? AND direction='inbound'
            ORDER BY received_at_utc DESC
            LIMIT ?
        """
        with self._lock:
            return list(self._conn.execute(query, (str(chat_id), limit)).fetchall())

    def save_summary(self, group_chat_id: int, window_start_utc: str, window_end_utc: str, summary_text: str) -> None:
        self._execute(
            """
            INSERT INTO summaries(group_chat_id, window_start_utc, window_end_utc, summary_text)
            VALUES (?, ?, ?, ?)
            """,
            (str(group_chat_id), window_start_utc, window_end_utc, summary_text),
        )

    def get_app_setting(self, key: str) -> str | None:
        with self._lock:
            row = self._conn.execute("SELECT value FROM app_settings WHERE key=?", (key,)).fetchone()
        if row is None:
            return None
        return str(row["value"])

    def set_app_setting(self, key: str, value: str) -> None:
        now = datetime.now(timezone.utc).isoformat()
        self._execute(
            """
            INSERT INTO app_settings(key, value, updated_at_utc)
            VALUES (?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at_utc=excluded.updated_at_utc
            """,
            (key, value, now),
        )
