"""Сховище на SQLite: нормалізована схема, цілісність, параметризовані запити.

Усі запити параметризовані (`?`) — значення ніколи не підставляються в SQL
рядковою конкатенацією, тому SQL-ін'єкції неможливі.
"""
from __future__ import annotations

import math
import sqlite3
import threading
import time

from .models import METRICS, Alert, Reading

SCHEMA = """
CREATE TABLE IF NOT EXISTS devices (
    id       INTEGER PRIMARY KEY,
    name     TEXT NOT NULL UNIQUE CHECK (length(name) BETWEEN 1 AND 64),
    location TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS metrics (
    id        INTEGER PRIMARY KEY,
    code      TEXT NOT NULL UNIQUE,
    unit      TEXT NOT NULL,
    min_value REAL NOT NULL,
    max_value REAL NOT NULL,
    CHECK (min_value < max_value)
);
CREATE TABLE IF NOT EXISTS readings (
    id        INTEGER PRIMARY KEY,
    device_id INTEGER NOT NULL REFERENCES devices(id) ON DELETE CASCADE,
    metric_id INTEGER NOT NULL REFERENCES metrics(id),
    value     REAL NOT NULL,
    ts        REAL NOT NULL CHECK (ts > 0),
    UNIQUE (device_id, metric_id, ts)  -- також є індексом для пошуку/JOIN
);
CREATE INDEX IF NOT EXISTS idx_readings_ts ON readings(ts);
CREATE TABLE IF NOT EXISTS alerts (
    id        INTEGER PRIMARY KEY,
    device_id INTEGER NOT NULL REFERENCES devices(id) ON DELETE CASCADE,
    metric_id INTEGER NOT NULL REFERENCES metrics(id),
    level     TEXT NOT NULL CHECK (level IN ('warning', 'critical', 'recovered')),
    value     REAL NOT NULL,
    ts        REAL NOT NULL,
    message   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_alerts_ts ON alerts(ts);
CREATE TABLE IF NOT EXISTS outbox (
    id         INTEGER PRIMARY KEY,
    reading_id INTEGER NOT NULL UNIQUE REFERENCES readings(id) ON DELETE CASCADE,
    attempts   INTEGER NOT NULL DEFAULT 0,
    sent_at    REAL
);
CREATE INDEX IF NOT EXISTS idx_outbox_pending ON outbox(id) WHERE sent_at IS NULL;
"""

MAX_LIMIT = 10000


def _check_limit(limit: int) -> None:
    if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= MAX_LIMIT:
        raise ValueError(f"limit має бути цілим у [1, {MAX_LIMIT}]")


class Storage:
    """Потокобезпечне сховище (одне з'єднання + блокування)."""

    def __init__(self, path: str = ":memory:"):
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.execute("PRAGMA foreign_keys = ON")
            if path != ":memory:":
                self._conn.execute("PRAGMA journal_mode = WAL")
            self._conn.executescript(SCHEMA)
            with self._conn:
                self._conn.executemany(
                    "INSERT OR IGNORE INTO metrics(code, unit, min_value, max_value) "
                    "VALUES (?, ?, ?, ?)",
                    [(m.code, m.unit, m.min_value, m.max_value) for m in METRICS.values()],
                )

    def close(self) -> None:
        """Закриває з'єднання."""
        with self._lock:
            self._conn.close()

    # --- допоміжне -------------------------------------------------------
    def _device_id(self, name: str, location: str = "") -> int:
        row = self._conn.execute("SELECT id FROM devices WHERE name = ?", (name,)).fetchone()
        if row:
            return row["id"]
        cur = self._conn.execute(
            "INSERT INTO devices(name, location) VALUES (?, ?)", (name, location)
        )
        return cur.lastrowid

    def _metric_id(self, code: str) -> int:
        row = self._conn.execute("SELECT id FROM metrics WHERE code = ?", (code,)).fetchone()
        if row is None:
            raise ValueError(f"невідома метрика: {code!r}")
        return row["id"]

    # --- запис -----------------------------------------------------------
    def save_reading(self, reading: Reading) -> bool:
        """Атомарно зберігає показання та ставить його в чергу відправки (outbox).

        Повертає False, якщо таке показання вже є (дублікат).
        Інші порушення цілісності не ігноруються.
        """
        try:
            with self._lock, self._conn:  # одна транзакція: commit / rollback
                device_id = self._device_id(reading.device)
                metric_id = self._metric_id(reading.metric)
                cur = self._conn.execute(
                    "INSERT INTO readings(device_id, metric_id, value, ts) VALUES (?, ?, ?, ?)",
                    (device_id, metric_id, reading.value, reading.ts),
                )
                self._conn.execute("INSERT INTO outbox(reading_id) VALUES (?)", (cur.lastrowid,))
                return True
        except sqlite3.IntegrityError as exc:
            if "UNIQUE" in str(exc):
                return False
            raise

    def save_alert(self, alert: Alert) -> None:
        """Зберігає сповіщення."""
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO alerts(device_id, metric_id, level, value, ts, message) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    self._device_id(alert.device),
                    self._metric_id(alert.metric),
                    alert.level,
                    alert.value,
                    alert.ts,
                    alert.message,
                ),
            )

    # --- читання ---------------------------------------------------------
    def list_devices(self) -> list[dict]:
        """Усі відомі пристрої."""
        with self._lock:
            rows = self._conn.execute("SELECT name, location FROM devices ORDER BY name")
            return [dict(r) for r in rows]

    def latest(self, device: str | None = None) -> list[dict]:
        """Останнє показання кожної пари (пристрій, метрика)."""
        sql = (
            "SELECT d.name AS device, m.code AS metric, m.unit AS unit, r.value, r.ts "
            "FROM readings r "
            "JOIN devices d ON d.id = r.device_id "
            "JOIN metrics m ON m.id = r.metric_id "
            "WHERE r.ts = (SELECT MAX(r2.ts) FROM readings r2 "
            "              WHERE r2.device_id = r.device_id AND r2.metric_id = r.metric_id) "
            "AND (? IS NULL OR d.name = ?) "
            "ORDER BY d.name, m.code"
        )
        with self._lock:
            return [dict(r) for r in self._conn.execute(sql, (device, device))]

    def query_readings(
        self,
        device: str,
        metric: str,
        since: float | None = None,
        until: float | None = None,
        limit: int = 100,
    ) -> list[dict]:
        """Показання пристрою/метрики за інтервалом, найновіші першими."""
        _check_limit(limit)
        sql = (
            "SELECT r.ts, r.value FROM readings r "
            "JOIN devices d ON d.id = r.device_id "
            "JOIN metrics m ON m.id = r.metric_id "
            "WHERE d.name = ? AND m.code = ? "
            "AND (? IS NULL OR r.ts >= ?) AND (? IS NULL OR r.ts <= ?) "
            "ORDER BY r.ts DESC LIMIT ?"
        )
        with self._lock:
            rows = self._conn.execute(sql, (device, metric, since, since, until, until, limit))
            return [dict(r) for r in rows]

    def aggregate(
        self,
        device: str,
        metric: str,
        bucket_s: float = 60.0,
        since: float | None = None,
        until: float | None = None,
    ) -> list[dict]:
        """Агрегація (avg/min/max/count) по часових вікнах bucket_s секунд."""
        if not math.isfinite(bucket_s) or bucket_s <= 0:
            raise ValueError("bucket_s має бути > 0")
        sql = (
            "SELECT CAST(r.ts / ? AS INTEGER) * ? AS bucket, "
            "AVG(r.value) AS avg, MIN(r.value) AS min, MAX(r.value) AS max, COUNT(*) AS count "
            "FROM readings r "
            "JOIN devices d ON d.id = r.device_id "
            "JOIN metrics m ON m.id = r.metric_id "
            "WHERE d.name = ? AND m.code = ? "
            "AND (? IS NULL OR r.ts >= ?) AND (? IS NULL OR r.ts <= ?) "
            "GROUP BY bucket ORDER BY bucket"
        )
        params = (bucket_s, bucket_s, device, metric, since, since, until, until)
        with self._lock:
            return [dict(r) for r in self._conn.execute(sql, params)]

    def recent_alerts(self, limit: int = 50) -> list[dict]:
        """Останні сповіщення."""
        _check_limit(limit)
        sql = (
            "SELECT d.name AS device, m.code AS metric, a.level, a.value, a.ts, a.message "
            "FROM alerts a JOIN devices d ON d.id = a.device_id "
            "JOIN metrics m ON m.id = a.metric_id ORDER BY a.ts DESC, a.id DESC LIMIT ?"
        )
        with self._lock:
            return [dict(r) for r in self._conn.execute(sql, (limit,))]

    # --- store-and-forward ----------------------------------------------
    def pending_batch(self, limit: int = 100) -> list[dict]:
        """Невідправлені показання (найстаріші першими)."""
        _check_limit(limit)
        sql = (
            "SELECT o.id AS outbox_id, d.name AS device, m.code AS metric, r.value, r.ts "
            "FROM outbox o JOIN readings r ON r.id = o.reading_id "
            "JOIN devices d ON d.id = r.device_id "
            "JOIN metrics m ON m.id = r.metric_id "
            "WHERE o.sent_at IS NULL ORDER BY o.id LIMIT ?"
        )
        with self._lock:
            return [dict(r) for r in self._conn.execute(sql, (limit,))]

    def mark_sent(self, outbox_ids: list[int], sent_at: float | None = None) -> None:
        """Позначає записи outbox як відправлені."""
        sent_at = time.time() if sent_at is None else sent_at
        with self._lock, self._conn:
            self._conn.executemany(
                "UPDATE outbox SET sent_at = ? WHERE id = ?", [(sent_at, i) for i in outbox_ids]
            )

    def mark_failed(self, outbox_ids: list[int]) -> None:
        """Збільшує лічильник невдалих спроб."""
        with self._lock, self._conn:
            self._conn.executemany(
                "UPDATE outbox SET attempts = attempts + 1 WHERE id = ?",
                [(i,) for i in outbox_ids],
            )

    def purge_sent(self, before_ts: float) -> int:
        """Видаляє вже відправлені показання, старіші за before_ts (ретеншн)."""
        with self._lock, self._conn:
            cur = self._conn.execute(
                "DELETE FROM readings WHERE ts < ? AND id IN "
                "(SELECT reading_id FROM outbox WHERE sent_at IS NOT NULL)",
                (before_ts,),
            )
            return cur.rowcount

    def stats(self) -> dict:
        """Лічильники для /stats та звітів."""
        queries = {
            "devices": "SELECT COUNT(*) FROM devices",
            "readings": "SELECT COUNT(*) FROM readings",
            "alerts": "SELECT COUNT(*) FROM alerts",
            "outbox_pending": "SELECT COUNT(*) FROM outbox WHERE sent_at IS NULL",
            "outbox_sent": "SELECT COUNT(*) FROM outbox WHERE sent_at IS NOT NULL",
        }
        with self._lock:
            return {k: self._conn.execute(q).fetchone()[0] for k, q in queries.items()}
