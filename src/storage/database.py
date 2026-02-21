import sqlite3
import threading
import re
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
                topic TEXT,
                notes TEXT,
                link TEXT,
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
            CREATE TABLE IF NOT EXISTS topics (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id_to_notify TEXT NOT NULL,
                display_name TEXT NOT NULL,
                internal_name TEXT NOT NULL,
                created_at_utc TEXT NOT NULL,
                UNIQUE(chat_id_to_notify, internal_name)
            );
            """,
            """
            CREATE TABLE IF NOT EXISTS reminder_topics (
                reminder_id INTEGER NOT NULL,
                topic_id INTEGER NOT NULL,
                created_at_utc TEXT NOT NULL,
                PRIMARY KEY(reminder_id, topic_id),
                FOREIGN KEY(reminder_id) REFERENCES reminders(id) ON DELETE CASCADE,
                FOREIGN KEY(topic_id) REFERENCES topics(id) ON DELETE CASCADE
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
            """
            CREATE TABLE IF NOT EXISTS calendar_sync (
                reminder_id INTEGER PRIMARY KEY,
                provider TEXT NOT NULL,
                event_id TEXT NOT NULL,
                last_synced_at_utc TEXT NOT NULL,
                FOREIGN KEY(reminder_id) REFERENCES reminders(id)
            );
            """,
            """
            CREATE TABLE IF NOT EXISTS calendar_sync_tombstones (
                provider TEXT NOT NULL,
                event_id TEXT NOT NULL,
                deleted_at_utc TEXT NOT NULL,
                PRIMARY KEY(provider, event_id)
            );
            """,
            "CREATE INDEX IF NOT EXISTS idx_messages_chat_received ON messages(chat_id, received_at_utc);",
            "CREATE INDEX IF NOT EXISTS idx_reminders_status_due ON reminders(status, due_at_utc);",
            "CREATE INDEX IF NOT EXISTS idx_reminders_status_priority_due ON reminders(status, priority, due_at_utc);",
        ]
        with self._lock:
            for stmt in statements:
                self._conn.execute(stmt)
            self._ensure_column("reminders", "link", "TEXT")
            self._ensure_column("reminders", "topic", "TEXT")
            self._migrate_legacy_topics()
            self._conn.commit()

    def _migrate_legacy_topics(self) -> None:
        rows = self._conn.execute(
            """
            SELECT id, chat_id_to_notify, topic
            FROM reminders
            WHERE TRIM(COALESCE(topic, '')) != ''
            """
        ).fetchall()
        for row in rows:
            reminder_id = int(row["id"])
            chat_id = str(row["chat_id_to_notify"])
            raw_topic = str(row["topic"] or "").strip()
            if not raw_topic:
                continue
            topic_id = self._get_latest_topic_id_by_display(chat_id, raw_topic)
            if topic_id is None:
                topic_id = self._create_topic_for_chat(chat_id, raw_topic)
            now = datetime.now(timezone.utc).isoformat()
            self._conn.execute(
                """
                INSERT OR IGNORE INTO reminder_topics(reminder_id, topic_id, created_at_utc)
                VALUES (?, ?, ?)
                """,
                (reminder_id, topic_id, now),
            )

    def _ensure_column(self, table_name: str, column_name: str, column_type: str) -> None:
        rows = self._conn.execute(f"PRAGMA table_info({table_name})").fetchall()
        existing_columns = {str(row[1]) for row in rows}
        if column_name in existing_columns:
            return
        self._conn.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_type}")

    def _execute(self, query: str, params: Iterable[Any] = ()) -> sqlite3.Cursor:
        with self._lock:
            cursor = self._conn.execute(query, tuple(params))
            self._conn.commit()
            return cursor

    def _create_topic_for_chat(self, chat_id_to_notify: str, display_name: str) -> int:
        now = datetime.now(timezone.utc).isoformat()
        normalized_display = display_name.strip()
        base_internal = normalized_display
        internal_name = base_internal
        counter = 1
        while True:
            existing = self._conn.execute(
                """
                SELECT 1 FROM topics
                WHERE chat_id_to_notify = ? AND lower(internal_name) = lower(?)
                LIMIT 1
                """,
                (chat_id_to_notify, internal_name),
            ).fetchone()
            if existing is None:
                break
            internal_name = f"{base_internal}({counter})"
            counter += 1
        cursor = self._conn.execute(
            """
            INSERT INTO topics(chat_id_to_notify, display_name, internal_name, created_at_utc)
            VALUES (?, ?, ?, ?)
            """,
            (chat_id_to_notify, normalized_display, internal_name, now),
        )
        return int(cursor.lastrowid)

    def _get_latest_topic_id_by_display(self, chat_id_to_notify: str, display_name: str) -> int | None:
        row = self._conn.execute(
            """
            SELECT id
            FROM topics
            WHERE chat_id_to_notify = ?
              AND lower(display_name) = lower(?)
            ORDER BY id DESC
            LIMIT 1
            """,
            (chat_id_to_notify, display_name.strip()),
        ).fetchone()
        if row is None:
            return None
        return int(row["id"])

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
        topic: str,
        notes: str,
        priority: str,
        due_at_utc: str,
        timezone_name: str,
        chat_id_to_notify: int,
        recurrence_rule: str | None,
        link: str = "",
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
                topic,
                notes,
                link,
                status,
                priority,
                due_at_utc,
                timezone,
                recurrence_rule,
                chat_id_to_notify,
                created_at_utc,
                updated_at_utc
            ) VALUES (?, ?, ?, ?, ?, ?, ?, 'open', ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                user_id,
                source_message_id,
                source_kind,
                title,
                topic,
                notes,
                link,
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

    def mark_done_and_archive_for_chat(self, reminder_id: int, chat_id_to_notify: int) -> bool:
        now = datetime.now(timezone.utc).isoformat()
        cursor = self._execute(
            """
            UPDATE reminders
            SET status='archived',
                done_at_utc=?,
                archived_at_utc=?,
                updated_at_utc=?
            WHERE id=?
              AND chat_id_to_notify=?
              AND status IN ('open', 'done')
            """,
            (now, now, now, reminder_id, str(chat_id_to_notify)),
        )
        return cursor.rowcount > 0

    def delete_reminder_permanently_for_chat(self, reminder_id: int, chat_id_to_notify: int) -> bool:
        with self._lock:
            existing = self._conn.execute(
                "SELECT id FROM reminders WHERE id = ? AND chat_id_to_notify = ?",
                (reminder_id, str(chat_id_to_notify)),
            ).fetchone()
            if existing is None:
                return False
            self._conn.execute("DELETE FROM calendar_sync WHERE reminder_id = ?", (reminder_id,))
            self._conn.execute("DELETE FROM reminder_topics WHERE reminder_id = ?", (reminder_id,))
            cursor = self._conn.execute(
                "DELETE FROM reminders WHERE id = ? AND chat_id_to_notify = ?",
                (reminder_id, str(chat_id_to_notify)),
            )
            self._conn.commit()
        return cursor.rowcount > 0

    def get_reminder_by_id(self, reminder_id: int) -> sqlite3.Row | None:
        with self._lock:
            row = self._conn.execute(
                """
                SELECT
                    r.id,
                    r.title,
                    r.topic,
                    COALESCE((
                        SELECT GROUP_CONCAT(DISTINCT t.display_name)
                        FROM reminder_topics rt
                        JOIN topics t ON t.id = rt.topic_id
                        WHERE rt.reminder_id = r.id
                    ), '') AS topics_text,
                    r.notes,
                    r.link,
                    r.priority,
                    r.due_at_utc,
                    r.status,
                    r.source_kind,
                    r.recurrence_rule,
                    r.created_at_utc,
                    r.updated_at_utc
                FROM reminders r
                WHERE r.id = ?
                """,
                (reminder_id,),
            ).fetchone()
        return row

    def get_calendar_event_id(self, reminder_id: int, provider: str = "google") -> str | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT event_id FROM calendar_sync WHERE reminder_id = ? AND provider = ?",
                (reminder_id, provider),
            ).fetchone()
        if row is None:
            return None
        return str(row["event_id"])

    def upsert_calendar_event_id(self, reminder_id: int, event_id: str, provider: str = "google") -> None:
        now = datetime.now(timezone.utc).isoformat()
        self._execute(
            """
            INSERT INTO calendar_sync(reminder_id, provider, event_id, last_synced_at_utc)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(reminder_id) DO UPDATE SET
                provider = excluded.provider,
                event_id = excluded.event_id,
                last_synced_at_utc = excluded.last_synced_at_utc
            """,
            (reminder_id, provider, event_id, now),
        )

    def delete_calendar_event_id(self, reminder_id: int, provider: str = "google") -> None:
        self._execute(
            "DELETE FROM calendar_sync WHERE reminder_id = ? AND provider = ?",
            (reminder_id, provider),
        )

    def get_reminder_id_by_calendar_event_id(self, event_id: str, provider: str = "google") -> int | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT reminder_id FROM calendar_sync WHERE event_id = ? AND provider = ?",
                (event_id, provider),
            ).fetchone()
        if row is None:
            return None
        return int(row["reminder_id"])

    def add_calendar_event_tombstone(self, event_id: str, provider: str = "google") -> None:
        event_id = str(event_id or "").strip()
        if not event_id:
            return
        now = datetime.now(timezone.utc).isoformat()
        self._execute(
            """
            INSERT INTO calendar_sync_tombstones(provider, event_id, deleted_at_utc)
            VALUES (?, ?, ?)
            ON CONFLICT(provider, event_id) DO UPDATE SET deleted_at_utc = excluded.deleted_at_utc
            """,
            (provider, event_id, now),
        )

    def is_calendar_event_tombstoned(self, event_id: str, provider: str = "google", ttl_days: int = 30) -> bool:
        event_id = str(event_id or "").strip()
        if not event_id:
            return False
        with self._lock:
            row = self._conn.execute(
                "SELECT deleted_at_utc FROM calendar_sync_tombstones WHERE provider = ? AND event_id = ?",
                (provider, event_id),
            ).fetchone()
            if row is None:
                return False
            deleted_text = str(row["deleted_at_utc"] or "").strip()
            try:
                deleted_dt = datetime.fromisoformat(deleted_text)
                if deleted_dt.tzinfo is None:
                    deleted_dt = deleted_dt.replace(tzinfo=timezone.utc)
            except Exception:
                return True
            return datetime.now(timezone.utc) - deleted_dt <= timedelta(days=max(1, ttl_days))

    def cleanup_calendar_tombstones(self, ttl_days: int = 30) -> int:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=max(1, ttl_days))).isoformat()
        cursor = self._execute(
            "DELETE FROM calendar_sync_tombstones WHERE deleted_at_utc < ?",
            (cutoff,),
        )
        return int(cursor.rowcount or 0)

    def get_reminder_by_id_for_chat(self, reminder_id: int, chat_id_to_notify: int) -> sqlite3.Row | None:
        with self._lock:
            row = self._conn.execute(
                """
                SELECT
                    r.id,
                    r.title,
                    r.topic,
                    COALESCE((
                        SELECT GROUP_CONCAT(DISTINCT t.display_name)
                        FROM reminder_topics rt
                        JOIN topics t ON t.id = rt.topic_id
                        WHERE rt.reminder_id = r.id
                    ), '') AS topics_text,
                    r.notes,
                    r.link,
                    r.priority,
                    r.due_at_utc,
                    r.status,
                    r.source_kind,
                    r.recurrence_rule,
                    r.created_at_utc,
                    r.updated_at_utc
                FROM reminders r
                WHERE r.id = ? AND r.chat_id_to_notify = ?
                """,
                (reminder_id, str(chat_id_to_notify)),
            ).fetchone()
        return row

    def update_reminder_fields(
        self,
        reminder_id: int,
        title: str,
        topic: str,
        notes: str,
        link: str,
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
                topic = ?,
                notes = ?,
                link = ?,
                priority = ?,
                due_at_utc = ?,
                recurrence_rule = ?,
                updated_at_utc = ?
            WHERE id = ?
            """,
            (title, topic, notes, link, normalized_priority, due_at_utc, recurrence_rule, now, reminder_id),
        )
        return cursor.rowcount > 0

    def update_reminder_fields_for_chat(
        self,
        reminder_id: int,
        chat_id_to_notify: int,
        title: str,
        topic: str,
        notes: str,
        link: str,
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
                topic = ?,
                notes = ?,
                link = ?,
                priority = ?,
                due_at_utc = ?,
                recurrence_rule = ?,
                updated_at_utc = ?
            WHERE id = ?
              AND chat_id_to_notify = ?
            """,
            (
                title,
                topic,
                notes,
                link,
                normalized_priority,
                due_at_utc,
                recurrence_rule,
                now,
                reminder_id,
                str(chat_id_to_notify),
            ),
        )
        return cursor.rowcount > 0

    def list_reminders(self, mode: str, value: str | None = None) -> list[sqlite3.Row]:
        base = """
            SELECT
                r.id,
                r.title,
                r.topic,
                COALESCE((
                    SELECT GROUP_CONCAT(DISTINCT t.display_name)
                    FROM reminder_topics rt
                    JOIN topics t ON t.id = rt.topic_id
                    WHERE rt.reminder_id = r.id
                ), '') AS topics_text,
                r.priority,
                r.due_at_utc,
                r.status
            FROM reminders r
            WHERE r.status='open'
        """
        params: list[Any] = []
        now = datetime.now(timezone.utc)

        if mode == "priority" and value:
            base += " AND r.priority = ?"
            params.append(value)
        elif mode == "topic" and value:
            base += " AND EXISTS (SELECT 1 FROM reminder_topics rt JOIN topics t ON t.id = rt.topic_id WHERE rt.reminder_id = r.id AND lower(t.display_name) = lower(?))"
            params.append(value)
        elif mode == "today":
            start = now.replace(hour=0, minute=0, second=0, microsecond=0)
            end = start + timedelta(days=1)
            base += " AND r.due_at_utc >= ? AND r.due_at_utc < ?"
            params.extend([start.isoformat(), end.isoformat()])
        elif mode == "tomorrow":
            start = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
            end = start + timedelta(days=1)
            base += " AND r.due_at_utc >= ? AND r.due_at_utc < ?"
            params.extend([start.isoformat(), end.isoformat()])
        elif mode == "overdue":
            base += " AND r.due_at_utc != '' AND r.due_at_utc < ?"
            params.append(now.isoformat())
        elif mode == "due_days" and value:
            days = int(value)
            end = now + timedelta(days=days)
            base += " AND r.due_at_utc >= ? AND r.due_at_utc <= ?"
            params.extend([now.isoformat(), end.isoformat()])

        base += (
            " ORDER BY CASE WHEN r.due_at_utc = '' THEN 1 ELSE 0 END ASC, r.due_at_utc ASC, "
            "CASE r.priority WHEN 'immediate' THEN 4 WHEN 'high' THEN 3 WHEN 'mid' THEN 2 ELSE 1 END DESC, "
            "r.id ASC"
        )

        with self._lock:
            return list(self._conn.execute(base, tuple(params)).fetchall())

    def list_reminders_for_chat(self, chat_id_to_notify: int) -> list[sqlite3.Row]:
        query = """
            SELECT
                r.id,
                r.title,
                r.topic,
                COALESCE((
                    SELECT GROUP_CONCAT(DISTINCT t.display_name)
                    FROM reminder_topics rt
                    JOIN topics t ON t.id = rt.topic_id
                    WHERE rt.reminder_id = r.id
                ), '') AS topics_text,
                r.priority,
                r.due_at_utc,
                r.status
            FROM reminders r
            WHERE r.status = 'open'
              AND r.chat_id_to_notify = ?
            ORDER BY CASE WHEN r.due_at_utc = '' THEN 1 ELSE 0 END ASC,
                     r.due_at_utc ASC,
                     CASE r.priority WHEN 'immediate' THEN 4 WHEN 'high' THEN 3 WHEN 'mid' THEN 2 ELSE 1 END DESC,
                     r.id ASC
        """
        with self._lock:
            return list(self._conn.execute(query, (str(chat_id_to_notify),)).fetchall())

    def list_reminders_between(self, start_utc_iso: str, end_utc_iso: str) -> list[sqlite3.Row]:
        query = """
            SELECT
                r.id,
                r.title,
                r.topic,
                COALESCE((
                    SELECT GROUP_CONCAT(DISTINCT t.display_name)
                    FROM reminder_topics rt
                    JOIN topics t ON t.id = rt.topic_id
                    WHERE rt.reminder_id = r.id
                ), '') AS topics_text,
                r.priority,
                r.due_at_utc,
                r.status
            FROM reminders r
            WHERE r.status = 'open'
              AND r.due_at_utc != ''
              AND r.due_at_utc >= ?
              AND r.due_at_utc < ?
            ORDER BY r.due_at_utc ASC,
                     CASE r.priority WHEN 'immediate' THEN 4 WHEN 'high' THEN 3 WHEN 'mid' THEN 2 ELSE 1 END DESC,
                     r.id ASC
        """
        with self._lock:
            return list(self._conn.execute(query, (start_utc_iso, end_utc_iso)).fetchall())

    def list_reminders_before(self, before_utc_iso: str) -> list[sqlite3.Row]:
        query = """
            SELECT
                r.id,
                r.title,
                r.topic,
                COALESCE((
                    SELECT GROUP_CONCAT(DISTINCT t.display_name)
                    FROM reminder_topics rt
                    JOIN topics t ON t.id = rt.topic_id
                    WHERE rt.reminder_id = r.id
                ), '') AS topics_text,
                r.priority,
                r.due_at_utc,
                r.status
            FROM reminders r
            WHERE r.status = 'open'
              AND r.due_at_utc != ''
              AND r.due_at_utc < ?
            ORDER BY r.due_at_utc ASC,
                     CASE r.priority WHEN 'immediate' THEN 4 WHEN 'high' THEN 3 WHEN 'mid' THEN 2 ELSE 1 END DESC,
                     r.id ASC
        """
        with self._lock:
            return list(self._conn.execute(query, (before_utc_iso,)).fetchall())

    def list_archived_reminders_for_chat(self, chat_id_to_notify: int, topic: str | None = None) -> list[sqlite3.Row]:
        query = """
            SELECT
                r.id,
                r.title,
                r.topic,
                COALESCE((
                    SELECT GROUP_CONCAT(DISTINCT t.display_name)
                    FROM reminder_topics rt
                    JOIN topics t ON t.id = rt.topic_id
                    WHERE rt.reminder_id = r.id
                ), '') AS topics_text,
                r.priority,
                r.due_at_utc,
                r.status
            FROM reminders r
            WHERE r.status = 'archived'
              AND r.chat_id_to_notify = ?
        """
        params: list[Any] = [str(chat_id_to_notify)]
        if topic and topic.strip():
            query += " AND EXISTS (SELECT 1 FROM reminder_topics rt JOIN topics t ON t.id = rt.topic_id WHERE rt.reminder_id = r.id AND lower(t.display_name) = lower(?))"
            params.append(topic.strip())
        query += " ORDER BY r.archived_at_utc DESC, r.id DESC"
        with self._lock:
            return list(self._conn.execute(query, tuple(params)).fetchall())

    def list_topic_index_for_chat(self, chat_id_to_notify: int, include_archived: bool = False) -> list[sqlite3.Row]:
        query = """
            SELECT
                t.id,
                t.display_name,
                t.internal_name,
                SUM(CASE WHEN r.status = 'open' THEN 1 ELSE 0 END) AS open_count,
                SUM(CASE WHEN r.status = 'archived' THEN 1 ELSE 0 END) AS archived_count
            FROM topics t
            LEFT JOIN reminder_topics rt ON rt.topic_id = t.id
            LEFT JOIN reminders r ON r.id = rt.reminder_id
            WHERE t.chat_id_to_notify = ?
        """
        params: list[Any] = [str(chat_id_to_notify)]
        if include_archived:
            query += " AND (r.status IN ('open', 'archived') OR r.status IS NULL)"
        else:
            query += " AND (r.status = 'open' OR r.status IS NULL)"
        query += " GROUP BY t.id, t.display_name, t.internal_name ORDER BY lower(t.display_name), t.id"
        with self._lock:
            return list(self._conn.execute(query, tuple(params)).fetchall())

    def list_topic_names_for_chat(self, chat_id_to_notify: int) -> list[str]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT DISTINCT display_name FROM topics WHERE chat_id_to_notify = ? ORDER BY lower(display_name)",
                (str(chat_id_to_notify),),
            ).fetchall()
        return [str(row["display_name"]) for row in rows]

    def create_topic_for_chat(self, chat_id_to_notify: int, display_name: str) -> int:
        normalized_display = display_name.strip()
        if not normalized_display:
            return 0
        with self._lock:
            return self._create_topic_for_chat(str(chat_id_to_notify), normalized_display)

    def rename_topic_for_chat(self, chat_id_to_notify: int, topic_id: int, new_display_name: str) -> bool:
        new_name = new_display_name.strip()
        if not new_name:
            return False
        with self._lock:
            row = self._conn.execute(
                "SELECT id, internal_name FROM topics WHERE id = ? AND chat_id_to_notify = ?",
                (topic_id, str(chat_id_to_notify)),
            ).fetchone()
            if row is None:
                return False
            internal_name = str(row["internal_name"] or "")
            base_old = re.sub(r"\(\d+\)$", "", internal_name).strip()
            suffix_match = re.search(r"\((\d+)\)$", internal_name)
            if suffix_match:
                new_internal = f"{new_name}({suffix_match.group(1)})"
            elif base_old and base_old.lower() != internal_name.lower():
                new_internal = new_name
            else:
                new_internal = new_name
            self._conn.execute(
                "UPDATE topics SET display_name = ?, internal_name = ? WHERE id = ?",
                (new_name, new_internal, topic_id),
            )
            self._conn.commit()
        return True

    def delete_topic_for_chat(self, chat_id_to_notify: int, topic_id: int) -> bool:
        with self._lock:
            cursor = self._conn.execute(
                "DELETE FROM topics WHERE id = ? AND chat_id_to_notify = ?",
                (topic_id, str(chat_id_to_notify)),
            )
            self._conn.commit()
        return cursor.rowcount > 0

    def merge_topics_for_chat(self, chat_id_to_notify: int, from_topic_id: int, to_topic_id: int) -> bool:
        if from_topic_id == to_topic_id:
            return False
        with self._lock:
            from_row = self._conn.execute(
                "SELECT id FROM topics WHERE id = ? AND chat_id_to_notify = ?",
                (from_topic_id, str(chat_id_to_notify)),
            ).fetchone()
            to_row = self._conn.execute(
                "SELECT id FROM topics WHERE id = ? AND chat_id_to_notify = ?",
                (to_topic_id, str(chat_id_to_notify)),
            ).fetchone()
            if from_row is None or to_row is None:
                return False
            reminder_rows = self._conn.execute(
                "SELECT reminder_id FROM reminder_topics WHERE topic_id = ?",
                (from_topic_id,),
            ).fetchall()
            now = datetime.now(timezone.utc).isoformat()
            for row in reminder_rows:
                self._conn.execute(
                    "INSERT OR IGNORE INTO reminder_topics(reminder_id, topic_id, created_at_utc) VALUES (?, ?, ?)",
                    (int(row["reminder_id"]), to_topic_id, now),
                )
            self._conn.execute("DELETE FROM reminder_topics WHERE topic_id = ?", (from_topic_id,))
            self._conn.execute("DELETE FROM topics WHERE id = ?", (from_topic_id,))
            self._conn.commit()
        return True

    def set_reminder_topics_for_chat(self, reminder_id: int, chat_id_to_notify: int, topics: list[str]) -> bool:
        normalized = [t.strip() for t in topics if t and t.strip()]
        with self._lock:
            reminder = self._conn.execute(
                "SELECT id FROM reminders WHERE id = ? AND chat_id_to_notify = ?",
                (reminder_id, str(chat_id_to_notify)),
            ).fetchone()
            if reminder is None:
                return False
            self._conn.execute("DELETE FROM reminder_topics WHERE reminder_id = ?", (reminder_id,))
            now = datetime.now(timezone.utc).isoformat()
            for name in normalized:
                topic_id = self._get_latest_topic_id_by_display(str(chat_id_to_notify), name)
                if topic_id is None:
                    continue
                self._conn.execute(
                    "INSERT OR IGNORE INTO reminder_topics(reminder_id, topic_id, created_at_utc) VALUES (?, ?, ?)",
                    (reminder_id, topic_id, now),
                )
            primary_topic = normalized[0] if normalized else ""
            self._conn.execute("UPDATE reminders SET topic = ? WHERE id = ?", (primary_topic, reminder_id))
            self._conn.commit()
        return True

    def add_topic_to_reminder_for_chat(self, reminder_id: int, chat_id_to_notify: int, display_name: str) -> bool:
        normalized = display_name.strip()
        if not normalized:
            return False
        with self._lock:
            topic_id = self._get_latest_topic_id_by_display(str(chat_id_to_notify), normalized)
            if topic_id is None:
                return False
            now = datetime.now(timezone.utc).isoformat()
            self._conn.execute(
                "INSERT OR IGNORE INTO reminder_topics(reminder_id, topic_id, created_at_utc) VALUES (?, ?, ?)",
                (reminder_id, topic_id, now),
            )
            self._conn.commit()
        return True

    def remove_one_topic_from_reminder_for_chat(self, reminder_id: int, chat_id_to_notify: int, display_name: str) -> bool:
        normalized = display_name.strip()
        if not normalized:
            return False
        with self._lock:
            row = self._conn.execute(
                """
                SELECT rt.topic_id
                FROM reminder_topics rt
                JOIN topics t ON t.id = rt.topic_id
                WHERE rt.reminder_id = ?
                  AND t.chat_id_to_notify = ?
                  AND lower(t.display_name) = lower(?)
                ORDER BY rt.topic_id DESC
                LIMIT 1
                """,
                (reminder_id, str(chat_id_to_notify), normalized),
            ).fetchone()
            if row is None:
                return False
            topic_id = int(row["topic_id"])
            cursor = self._conn.execute(
                "DELETE FROM reminder_topics WHERE reminder_id = ? AND topic_id = ?",
                (reminder_id, topic_id),
            )
            self._conn.commit()
        return cursor.rowcount > 0

    def has_missing_topics_for_chat(self, chat_id_to_notify: int, topics: list[str]) -> list[str]:
        missing: list[str] = []
        with self._lock:
            for raw in topics:
                name = raw.strip()
                if not name:
                    continue
                topic_id = self._get_latest_topic_id_by_display(str(chat_id_to_notify), name)
                if topic_id is None:
                    missing.append(name)
        return missing

    def suggest_topics_for_chat(self, chat_id_to_notify: int, topic_query: str, limit: int = 5) -> list[str]:
        query = topic_query.strip().lower()
        if not query:
            return []
        names = self.list_topic_names_for_chat(chat_id_to_notify)
        starts = [name for name in names if name.lower().startswith(query)]
        contains = [name for name in names if query in name.lower() and name not in starts]
        suggestions = (starts + contains)[:limit]
        return suggestions

    def get_due_reminders(self, now_utc_iso: str) -> list[sqlite3.Row]:
        query = """
            SELECT *
            FROM reminders
            WHERE status='open'
              AND due_at_utc != ''
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

    def fetch_recent_group_messages_since(self, group_chat_id: int, since_utc_iso: str, limit: int = 50) -> list[sqlite3.Row]:
        query = """
            SELECT text, sender_telegram_id, received_at_utc
            FROM messages
            WHERE chat_id = ?
              AND source_type='group'
              AND direction='inbound'
              AND received_at_utc > ?
            ORDER BY received_at_utc DESC
            LIMIT ?
        """
        with self._lock:
            return list(self._conn.execute(query, (str(group_chat_id), since_utc_iso, limit)).fetchall())

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
