"""SQLite schema, connection management, and WAL configuration."""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from app.config import get_settings


SCHEMA_SQL = """
PRAGMA foreign_keys = ON;
PRAGMA journal_mode = WAL;
PRAGMA synchronous = NORMAL;

CREATE TABLE IF NOT EXISTS customers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    normalized_name TEXT NOT NULL,
    phone_enc TEXT,
    reminder_opt_in INTEGER NOT NULL DEFAULT 0,
    preferred_language TEXT NOT NULL DEFAULT 'hi-IN',
    is_synthetic INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_customers_norm_name ON customers (normalized_name);

CREATE TABLE IF NOT EXISTS transactions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    customer_id INTEGER NOT NULL,
    amount REAL NOT NULL CHECK (amount > 0),
    tx_type TEXT NOT NULL CHECK (tx_type IN ('credit', 'payment')),
    transaction_date TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'confirmed' CHECK (status IN ('confirmed', 'pending', 'voided')),
    source_session_id TEXT,
    raw_utterance_enc TEXT,
    note TEXT,
    confidence REAL NOT NULL DEFAULT 1.0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (customer_id) REFERENCES customers(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_tx_customer_status ON transactions (customer_id, status);
CREATE INDEX IF NOT EXISTS idx_tx_date_type_status ON transactions (transaction_date, tx_type, status);

CREATE TABLE IF NOT EXISTS voice_sessions (
    id TEXT PRIMARY KEY,
    language TEXT NOT NULL DEFAULT 'hi-IN',
    turn_count INTEGER NOT NULL DEFAULT 0,
    ai_disclosed INTEGER NOT NULL DEFAULT 0,
    pending_action_json TEXT,
    last_committed_tx_ids_json TEXT,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS voice_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    channel TEXT NOT NULL DEFAULT 'voice',
    utterance_enc TEXT,
    parsed_json TEXT,
    ai_reply_enc TEXT,
    model_used TEXT,
    latency_ms INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_voice_logs_created ON voice_logs (created_at DESC);

CREATE TABLE IF NOT EXISTS audit_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    transaction_id INTEGER,
    action TEXT NOT NULL,
    old_value_json TEXT,
    new_value_json TEXT,
    reason TEXT,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_audit_tx ON audit_logs (transaction_id, created_at DESC);

CREATE TABLE IF NOT EXISTS app_settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


class Database:
    """Lightweight SQLite repository manager with deterministic transaction support."""

    def __init__(self, db_path: str | Path | None = None) -> None:
        self.db_path = Path(db_path or get_settings().database_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.init_schema()

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), timeout=10.0, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON;")
        return conn

    def init_schema(self) -> None:
        with self.connect() as conn:
            conn.executescript(SCHEMA_SQL)
            conn.commit()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        conn = self.connect()
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

