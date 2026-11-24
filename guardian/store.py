from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


class EventStore:
    """Small local incident ledger. It stores evidence, never credentials."""

    def __init__(self, database: Path) -> None:
        database.parent.mkdir(parents=True, exist_ok=True)
        self.database = database
        with self._connect() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS checks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    checked_at TEXT NOT NULL,
                    healthy INTEGER NOT NULL,
                    report_path TEXT NOT NULL,
                    summary TEXT NOT NULL
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS service_state (
                    service TEXT PRIMARY KEY,
                    consecutive_failures INTEGER NOT NULL DEFAULT 0,
                    last_restart_at TEXT
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS processed_ci_runs (
                    run_id INTEGER PRIMARY KEY,
                    processed_at TEXT NOT NULL,
                    outcome TEXT NOT NULL
                )
            """)
            cutoff = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
            conn.execute("DELETE FROM checks WHERE checked_at < ?", (cutoff,))

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.database)
        conn.row_factory = sqlite3.Row
        return conn

    def record_check(self, healthy: bool, report_path: str, summary: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO checks (checked_at, healthy, report_path, summary) VALUES (?, ?, ?, ?)",
                (datetime.now(timezone.utc).isoformat(), int(healthy), report_path, summary[:1000]),
            )

    def latest_report(self) -> Path | None:
        with self._connect() as conn:
            row = conn.execute("SELECT report_path FROM checks WHERE report_path <> '' ORDER BY id DESC LIMIT 1").fetchone()
        return Path(row["report_path"]) if row else None

    def failures(self, service: str) -> int:
        with self._connect() as conn:
            row = conn.execute("SELECT consecutive_failures FROM service_state WHERE service=?", (service,)).fetchone()
        return int(row[0]) if row else 0

    def set_failures(self, service: str, count: int) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO service_state(service, consecutive_failures) VALUES (?, ?) "
                "ON CONFLICT(service) DO UPDATE SET consecutive_failures=excluded.consecutive_failures",
                (service, count),
            )

    def last_restart(self, service: str) -> datetime | None:
        with self._connect() as conn:
            row = conn.execute("SELECT last_restart_at FROM service_state WHERE service=?", (service,)).fetchone()
        if not row or not row[0]:
            return None
        return datetime.fromisoformat(row[0])

    def ci_run_processed(self, run_id: int) -> bool:
        with self._connect() as conn:
            row = conn.execute("SELECT 1 FROM processed_ci_runs WHERE run_id=?", (run_id,)).fetchone()
        return row is not None

    def mark_ci_run_processed(self, run_id: int, outcome: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO processed_ci_runs(run_id, processed_at, outcome) VALUES (?, ?, ?) "
                "ON CONFLICT(run_id) DO NOTHING",
                (run_id, datetime.now(timezone.utc).isoformat(), outcome[:500]),
            )

    def record_restart(self, service: str, *, reset_failures: bool = True) -> None:
        """Record every recovery attempt so a failing command is also rate-limited."""
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO service_state(service, consecutive_failures, last_restart_at) VALUES (?, 0, ?) "
                "ON CONFLICT(service) DO UPDATE SET "
                "consecutive_failures=CASE WHEN ? THEN 0 ELSE service_state.consecutive_failures END, "
                "last_restart_at=excluded.last_restart_at",
                (service, now, int(reset_failures)),
            )
