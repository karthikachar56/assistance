"""Unified persistence stores (SQLite & MongoDB) sharing identical ledger contracts."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import threading
from typing import Any, Iterator, Protocol
from urllib.parse import urlparse

from pymongo import ASCENDING, DESCENDING, MongoClient, ReturnDocument

from app.db.database import Database


def normalize_name(name: str) -> str:
    """Normalize customer names for reliable case-insensitive fuzzy lookup."""
    return " ".join(name.strip().lower().split())


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _clean_doc(doc: dict[str, Any] | None) -> dict[str, Any] | None:
    """Strip internal Mongo ObjectId before returning documents to callers."""
    if doc is None:
        return None
    out = dict(doc)
    out.pop("_id", None)
    return out


def group_transactions_by_customer(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Aggregate confirmed transaction rows by customer, sorted highest amount first."""
    buckets: dict[int, dict[str, Any]] = {}
    for row in rows:
        cid = int(row["customer_id"])
        amt = float(row["amount"])
        entry = buckets.get(cid)
        if entry is None:
            buckets[cid] = {
                "customer_id": cid,
                "customer_name": str(row.get("customer_name") or f"Customer #{cid}"),
                "amount": amt,
                "tx_count": 1,
            }
        else:
            entry["amount"] += amt
            entry["tx_count"] += 1
    out = []
    for entry in buckets.values():
        entry["amount"] = round(float(entry["amount"]), 2)
        out.append(entry)
    out.sort(key=lambda item: (-item["amount"], item["customer_name"].lower()))
    return out


class KhataStore(Protocol):
    """Repository interface implemented identically by SQLite and MongoDB stores."""

    backend_name: str
    database_label: str

    @contextmanager
    def unit_of_work(self) -> Iterator[None]: ...
    def count_customers(self) -> int: ...
    def list_customers_raw(self) -> list[dict]: ...
    def list_customer_names(self) -> list[str]: ...
    def list_customers_with_totals(self, search_query: str = "") -> list[dict]: ...
    def get_customer_by_id(self, customer_id: int) -> dict | None: ...
    def get_customer_with_totals(self, customer_id: int) -> dict | None: ...
    def create_customer(
        self,
        *,
        name: str,
        phone_enc: str | None,
        reminder_opt_in: bool,
        preferred_language: str,
        is_synthetic: bool = False,
        created_at: str | None = None,
    ) -> dict: ...
    def update_customer(
        self,
        customer_id: int,
        *,
        name: str,
        phone_enc: str | None,
        reminder_opt_in: bool,
        preferred_language: str,
    ) -> dict | None: ...
    def delete_customer(self, customer_id: int) -> bool: ...
    def get_customer_balance(self, customer_id: int) -> float: ...
    def get_dashboard_summary(self, today_str: str, recent_limit: int = 12) -> dict: ...
    def list_customer_transactions(self, customer_id: int) -> list[dict]: ...
    def get_customer_recent_confirmed_transactions(self, customer_id: int, limit: int = 3) -> list[dict]: ...
    def list_date_transactions(self, tx_type: str | None, target_date: str) -> list[dict]: ...

    def get_date_tx_sum(self, tx_type: str, target_date: str) -> float: ...
    def get_total_outstanding(self) -> float: ...
    def get_top_debtor(self) -> tuple[str, float] | None: ...
    def get_transaction_with_customer(self, tx_id: int) -> dict | None: ...
    def create_transaction(
        self,
        *,
        customer_id: int,
        customer_name: str,
        amount: float,
        tx_type: str,
        transaction_date: str,
        status: str = "confirmed",
        source_session_id: str | None = None,
        raw_utterance_enc: str | None = None,
        note: str | None = None,
        confidence: float = 1.0,
        audit_action: str = "create",
        audit_reason: str = "manual_entry",
        audit_new_value: dict[str, Any] | None = None,
        created_at: str | None = None,
    ) -> dict: ...
    def correct_transaction(
        self,
        tx_id: int,
        *,
        new_amount: float,
        new_tx_type: str,
        old_amount: float,
        old_tx_type: str,
        reason: str,
        updated_at: str,
    ) -> None: ...
    def export_transactions(self, customer_id: int | None = None) -> list[dict]: ...
    def list_audit_logs(self, limit: int = 50) -> list[dict]: ...
    def get_or_create_session(self, session_id: str, language: str) -> dict: ...
    def get_pending_action(self, session_id: str) -> dict | None: ...
    def update_session(
        self,
        session_id: str,
        *,
        language: str,
        pending_action: dict | None,
        last_committed_tx_ids: list[int],
        updated_at: str,
    ) -> None: ...
    def insert_voice_log(
        self,
        *,
        session_id: str,
        channel: str,
        utterance_enc: str | None,
        parsed_json: str,
        ai_reply_enc: str | None,
        model_used: str,
        latency_ms: int,
        created_at: str,
    ) -> int: ...
    def update_voice_log_latency(self, voice_log_id: int, total_ms: int) -> None: ...
    def list_voice_history(self, limit: int = 40) -> list[dict]: ...
    def ensure_default_settings(self, defaults: list[tuple[str, str]]) -> None: ...
    def get_all_settings(self) -> dict[str, str]: ...
    def get_setting(self, key: str, default: str) -> str: ...
    def upsert_settings(self, updates: dict[str, str]) -> dict[str, str]: ...
    def wipe_demo_transcripts(self) -> int: ...


class SqliteKhataStore:
    """SQLite-backed store with thread-local unit-of-work and legacy connection helpers."""

    backend_name = "sqlite"

    def __init__(self, db_path: str | Path | None = None) -> None:
        self.db = Database(db_path=db_path)
        self.db_path = self.db.db_path
        self.database_label = self.db.db_path.name
        self._local = threading.local()

    def connect(self) -> sqlite3.Connection:
        """Legacy helper for SQLite-specific diagnostic tests."""
        return self.db.connect()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        """Legacy context manager for SQLite-specific diagnostic tests."""
        with self.db.transaction() as conn:
            yield conn

    @contextmanager
    def unit_of_work(self) -> Iterator[None]:
        existing = getattr(self._local, "conn", None)
        if existing is not None:
            yield
            return
        with self.db.transaction() as conn:
            self._local.conn = conn
            try:
                yield
            finally:
                self._local.conn = None

    @contextmanager
    def _conn(self, write: bool = False) -> Iterator[sqlite3.Connection]:
        active = getattr(self._local, "conn", None)
        if active is not None:
            yield active
            return
        if write:
            with self.db.transaction() as conn:
                yield conn
        else:
            with self.db.connect() as conn:
                yield conn

    def count_customers(self) -> int:
        with self._conn() as conn:
            return int(conn.execute("SELECT COUNT(*) FROM customers").fetchone()[0])

    def list_customers_raw(self) -> list[dict]:
        with self._conn() as conn:
            rows = conn.execute("SELECT * FROM customers ORDER BY name ASC").fetchall()
            return [dict(r) for r in rows]

    def list_customer_names(self) -> list[str]:
        with self._conn() as conn:
            rows = conn.execute("SELECT name FROM customers ORDER BY name ASC").fetchall()
            return [r["name"] for r in rows]

    def list_customers_with_totals(self, search_query: str = "") -> list[dict]:
        query = """
            SELECT c.*,
                COALESCE(SUM(CASE WHEN t.tx_type='credit' AND t.status='confirmed' THEN t.amount ELSE 0 END), 0) AS total_credit,
                COALESCE(SUM(CASE WHEN t.tx_type='payment' AND t.status='confirmed' THEN t.amount ELSE 0 END), 0) AS total_payment
            FROM customers c
            LEFT JOIN transactions t ON t.customer_id = c.id
        """
        params: tuple = ()
        if search_query.strip():
            query += " WHERE c.normalized_name LIKE ?"
            params = (f"%{normalize_name(search_query)}%",)
        query += " GROUP BY c.id ORDER BY c.name ASC"
        with self._conn() as conn:
            return [dict(r) for r in conn.execute(query, params).fetchall()]

    def get_customer_by_id(self, customer_id: int) -> dict | None:
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM customers WHERE id = ?", (customer_id,)).fetchone()
            return dict(row) if row else None

    def get_customer_with_totals(self, customer_id: int) -> dict | None:
        with self._conn() as conn:
            row = conn.execute(
                """
                SELECT c.*,
                    COALESCE(SUM(CASE WHEN t.tx_type='credit' AND t.status='confirmed' THEN t.amount ELSE 0 END), 0) AS total_credit,
                    COALESCE(SUM(CASE WHEN t.tx_type='payment' AND t.status='confirmed' THEN t.amount ELSE 0 END), 0) AS total_payment
                FROM customers c
                LEFT JOIN transactions t ON t.customer_id = c.id
                WHERE c.id = ?
                GROUP BY c.id
                """,
                (customer_id,),
            ).fetchone()
            return dict(row) if row else None

    def create_customer(
        self,
        *,
        name: str,
        phone_enc: str | None,
        reminder_opt_in: bool,
        preferred_language: str,
        is_synthetic: bool = False,
        created_at: str | None = None,
    ) -> dict:
        now = created_at or _utc_now_iso()
        clean_name = name.strip()
        with self._conn(write=True) as conn:
            cur = conn.execute(
                """
                INSERT INTO customers
                    (name, normalized_name, phone_enc, reminder_opt_in, preferred_language, is_synthetic, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    clean_name,
                    normalize_name(clean_name),
                    phone_enc,
                    1 if reminder_opt_in else 0,
                    preferred_language,
                    1 if is_synthetic else 0,
                    now,
                ),
            )
            cid = int(cur.lastrowid)
            row = dict(conn.execute("SELECT * FROM customers WHERE id = ?", (cid,)).fetchone())
        row["total_credit"] = 0.0
        row["total_payment"] = 0.0
        return row

    def update_customer(
        self,
        customer_id: int,
        *,
        name: str,
        phone_enc: str | None,
        reminder_opt_in: bool,
        preferred_language: str,
    ) -> dict | None:
        with self._conn(write=True) as conn:
            existing = conn.execute("SELECT id FROM customers WHERE id = ?", (customer_id,)).fetchone()
            if not existing:
                return None
            clean_name = name.strip()
            conn.execute(
                """
                UPDATE customers
                SET name = ?, normalized_name = ?, phone_enc = ?, reminder_opt_in = ?, preferred_language = ?
                WHERE id = ?
                """,
                (
                    clean_name,
                    normalize_name(clean_name),
                    phone_enc,
                    1 if reminder_opt_in else 0,
                    preferred_language,
                    customer_id,
                ),
            )
            row = conn.execute(
                """
                SELECT c.*,
                    COALESCE(SUM(CASE WHEN t.tx_type='credit' AND t.status='confirmed' THEN t.amount ELSE 0 END), 0) AS total_credit,
                    COALESCE(SUM(CASE WHEN t.tx_type='payment' AND t.status='confirmed' THEN t.amount ELSE 0 END), 0) AS total_payment
                FROM customers c
                LEFT JOIN transactions t ON t.customer_id = c.id
                WHERE c.id = ?
                GROUP BY c.id
                """,
                (customer_id,),
            ).fetchone()
            return dict(row) if row else None

    def delete_customer(self, customer_id: int) -> bool:
        with self._conn(write=True) as conn:
            existing = conn.execute("SELECT id, name FROM customers WHERE id = ?", (customer_id,)).fetchone()
            if not existing:
                return False
            conn.execute("DELETE FROM transactions WHERE customer_id = ?", (customer_id,))
            conn.execute("DELETE FROM customers WHERE id = ?", (customer_id,))
            return True

    def get_customer_balance(self, customer_id: int) -> float:
        with self._conn() as conn:
            row = conn.execute(
                """
                SELECT
                    COALESCE(SUM(CASE WHEN tx_type = 'credit' AND status = 'confirmed' THEN amount ELSE 0 END), 0) AS total_credit,
                    COALESCE(SUM(CASE WHEN tx_type = 'payment' AND status = 'confirmed' THEN amount ELSE 0 END), 0) AS total_payment
                FROM transactions
                WHERE customer_id = ?
                """,
                (customer_id,),
            ).fetchone()
            return round(float(row["total_credit"]) - float(row["total_payment"]), 2)

    def get_dashboard_summary(self, today_str: str, recent_limit: int = 12) -> dict:
        with self._conn() as conn:
            totals = conn.execute(
                """
                SELECT
                    COALESCE(SUM(CASE WHEN tx_type='credit' AND status='confirmed' AND transaction_date=? THEN amount ELSE 0 END), 0) AS today_credit,
                    COALESCE(SUM(CASE WHEN tx_type='payment' AND status='confirmed' AND transaction_date=? THEN amount ELSE 0 END), 0) AS today_payment,
                    COALESCE(SUM(CASE WHEN tx_type='credit' AND status='confirmed' THEN amount ELSE 0 END), 0)
                    - COALESCE(SUM(CASE WHEN tx_type='payment' AND status='confirmed' THEN amount ELSE 0 END), 0) AS total_outstanding
                FROM transactions
                """,
                (today_str, today_str),
            ).fetchone()
            cust_count = int(conn.execute("SELECT COUNT(*) FROM customers").fetchone()[0])
            recent_rows = conn.execute(
                """
                SELECT t.*, c.name AS customer_name
                FROM transactions t
                JOIN customers c ON c.id = t.customer_id
                WHERE t.status = 'confirmed'
                ORDER BY t.id DESC
                LIMIT ?
                """,
                (recent_limit,),
            ).fetchall()
            today_credit_rows = conn.execute(
                """
                SELECT t.customer_id, c.name AS customer_name, t.amount
                FROM transactions t
                JOIN customers c ON c.id = t.customer_id
                WHERE t.status = 'confirmed' AND t.tx_type = 'credit' AND t.transaction_date = ?
                ORDER BY t.id DESC
                """,
                (today_str,),
            ).fetchall()
            today_payment_rows = conn.execute(
                """
                SELECT t.customer_id, c.name AS customer_name, t.amount
                FROM transactions t
                JOIN customers c ON c.id = t.customer_id
                WHERE t.status = 'confirmed' AND t.tx_type = 'payment' AND t.transaction_date = ?
                ORDER BY t.id DESC
                """,
                (today_str,),
            ).fetchall()
            debtor_rows = conn.execute(
                """
                SELECT c.id AS customer_id, c.name AS customer_name,
                    ROUND(
                        COALESCE(SUM(CASE WHEN t.tx_type='credit' AND t.status='confirmed' THEN t.amount ELSE 0 END), 0)
                        - COALESCE(SUM(CASE WHEN t.tx_type='payment' AND t.status='confirmed' THEN t.amount ELSE 0 END), 0),
                        2
                    ) AS amount
                FROM customers c
                LEFT JOIN transactions t ON t.customer_id = c.id
                GROUP BY c.id
                HAVING amount > 0
                ORDER BY amount DESC, c.name ASC
                """
            ).fetchall()
        return {
            "today_credit": round(float(totals["today_credit"]), 2),
            "today_payment": round(float(totals["today_payment"]), 2),
            "total_outstanding": round(float(totals["total_outstanding"]), 2),
            "total_customers": cust_count,
            "recent_transactions": [dict(r) for r in recent_rows],
            "today_credit_customers": group_transactions_by_customer([dict(r) for r in today_credit_rows]),
            "today_payment_customers": group_transactions_by_customer([dict(r) for r in today_payment_rows]),
            "outstanding_customers": [
                {
                    "customer_id": int(r["customer_id"]),
                    "customer_name": r["customer_name"],
                    "amount": float(r["amount"]),
                    "tx_count": 1,
                }
                for r in debtor_rows
            ],
        }

    def list_customer_transactions(self, customer_id: int) -> list[dict]:
        with self._conn() as conn:
            rows = conn.execute(
                """
                SELECT t.*, c.name AS customer_name
                FROM transactions t
                JOIN customers c ON c.id = t.customer_id
                WHERE t.customer_id = ? AND t.status != 'voided'
                ORDER BY t.transaction_date DESC, t.id DESC
                """,
                (customer_id,),
            ).fetchall()
            return [dict(r) for r in rows]

    def get_customer_recent_confirmed_transactions(self, customer_id: int, limit: int = 3) -> list[dict]:
        with self._conn() as conn:
            rows = conn.execute(
                """
                SELECT amount, tx_type, transaction_date
                FROM transactions
                WHERE customer_id = ? AND status = 'confirmed'
                ORDER BY transaction_date DESC, id DESC
                LIMIT ?
                """,
                (customer_id, limit),
            ).fetchall()
            return [dict(r) for r in rows]

    def list_date_transactions(self, tx_type: str | None, target_date: str) -> list[dict]:
        with self._conn() as conn:
            if tx_type in ("credit", "payment"):
                rows = conn.execute(
                    """
                    SELECT t.*, c.name AS customer_name
                    FROM transactions t
                    JOIN customers c ON c.id = t.customer_id
                    WHERE t.status = 'confirmed' AND t.tx_type = ? AND t.transaction_date = ?
                    ORDER BY t.id DESC
                    """,
                    (tx_type, target_date),
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT t.*, c.name AS customer_name
                    FROM transactions t
                    JOIN customers c ON c.id = t.customer_id
                    WHERE t.status = 'confirmed' AND t.transaction_date = ?
                    ORDER BY t.id DESC
                    """,
                    (target_date,),
                ).fetchall()
            return [dict(r) for r in rows]

    def get_date_tx_sum(self, tx_type: str, target_date: str) -> float:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT COALESCE(SUM(amount), 0) FROM transactions WHERE tx_type = ? AND status = 'confirmed' AND transaction_date = ?",
                (tx_type, target_date),
            ).fetchone()
            return float(row[0])

    def get_total_outstanding(self) -> float:
        with self._conn() as conn:
            row = conn.execute(
                """
                SELECT
                    COALESCE(SUM(CASE WHEN tx_type = 'credit' AND status = 'confirmed' THEN amount ELSE 0 END), 0)
                    - COALESCE(SUM(CASE WHEN tx_type = 'payment' AND status = 'confirmed' THEN amount ELSE 0 END), 0)
                FROM transactions
                """
            ).fetchone()
            return round(float(row[0]), 2)

    def get_top_debtor(self) -> tuple[str, float] | None:
        with self._conn() as conn:
            row = conn.execute(
                """
                SELECT c.name,
                    COALESCE(SUM(CASE WHEN t.tx_type = 'credit' AND t.status = 'confirmed' THEN t.amount ELSE 0 END), 0)
                    - COALESCE(SUM(CASE WHEN t.tx_type = 'payment' AND t.status = 'confirmed' THEN t.amount ELSE 0 END), 0) AS bal
                FROM customers c
                LEFT JOIN transactions t ON t.customer_id = c.id
                GROUP BY c.id
                ORDER BY bal DESC
                LIMIT 1
                """
            ).fetchone()
            if not row:
                return None
            return row["name"], float(row["bal"])

    def get_transaction_with_customer(self, tx_id: int) -> dict | None:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT t.*, c.name AS customer_name FROM transactions t JOIN customers c ON c.id = t.customer_id WHERE t.id = ?",
                (tx_id,),
            ).fetchone()
            return dict(row) if row else None

    def create_transaction(
        self,
        *,
        customer_id: int,
        customer_name: str,
        amount: float,
        tx_type: str,
        transaction_date: str,
        status: str = "confirmed",
        source_session_id: str | None = None,
        raw_utterance_enc: str | None = None,
        note: str | None = None,
        confidence: float = 1.0,
        audit_action: str = "create",
        audit_reason: str = "manual_entry",
        audit_new_value: dict[str, Any] | None = None,
        created_at: str | None = None,
    ) -> dict:
        now = created_at or _utc_now_iso()
        rounded = round(float(amount), 2)
        audit_payload = audit_new_value or {
            "customer_id": customer_id,
            "amount": rounded,
            "tx_type": tx_type,
            "date": transaction_date,
        }
        with self._conn(write=True) as conn:
            cur = conn.execute(
                """
                INSERT INTO transactions
                    (customer_id, amount, tx_type, transaction_date, status, source_session_id,
                     raw_utterance_enc, note, confidence, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    customer_id,
                    rounded,
                    tx_type,
                    transaction_date,
                    status,
                    source_session_id,
                    raw_utterance_enc,
                    note,
                    confidence,
                    now,
                    now,
                ),
            )
            tx_id = int(cur.lastrowid)
            conn.execute(
                """
                INSERT INTO audit_logs (transaction_id, action, old_value_json, new_value_json, reason, created_at)
                VALUES (?, ?, NULL, ?, ?, ?)
                """,
                (tx_id, audit_action, json.dumps(audit_payload), audit_reason, now),
            )
        return {
            "id": tx_id,
            "customer_id": customer_id,
            "customer_name": customer_name,
            "amount": rounded,
            "tx_type": tx_type,
            "transaction_date": transaction_date,
            "status": status,
            "note": note,
            "confidence": float(confidence),
            "created_at": now,
        }

    def correct_transaction(
        self,
        tx_id: int,
        *,
        new_amount: float,
        new_tx_type: str,
        old_amount: float,
        old_tx_type: str,
        reason: str,
        updated_at: str,
    ) -> None:
        with self._conn(write=True) as conn:
            conn.execute(
                "UPDATE transactions SET amount = ?, tx_type = ?, updated_at = ? WHERE id = ?",
                (round(new_amount, 2), new_tx_type, updated_at, tx_id),
            )
            conn.execute(
                """
                INSERT INTO audit_logs (transaction_id, action, old_value_json, new_value_json, reason, created_at)
                VALUES (?, 'correct', ?, ?, ?, ?)
                """,
                (
                    tx_id,
                    json.dumps({"amount": old_amount, "tx_type": old_tx_type}),
                    json.dumps({"amount": new_amount, "tx_type": new_tx_type}),
                    reason,
                    updated_at,
                ),
            )

    def export_transactions(self, customer_id: int | None = None) -> list[dict]:
        query = """
            SELECT t.id, c.name AS customer_name, t.transaction_date, t.tx_type, t.amount, t.status, t.note, t.created_at
            FROM transactions t
            JOIN customers c ON c.id = t.customer_id
        """
        params: tuple = ()
        if customer_id is not None:
            query += " WHERE t.customer_id = ?"
            params = (customer_id,)
        query += " ORDER BY t.transaction_date DESC, t.id DESC"
        with self._conn() as conn:
            return [dict(r) for r in conn.execute(query, params).fetchall()]

    def list_audit_logs(self, limit: int = 50) -> list[dict]:
        capped = min(max(limit, 1), 200)
        with self._conn() as conn:
            rows = conn.execute("SELECT * FROM audit_logs ORDER BY id DESC LIMIT ?", (capped,)).fetchall()
            return [dict(r) for r in rows]

    def get_or_create_session(self, session_id: str, language: str) -> dict:
        with self._conn(write=True) as conn:
            row = conn.execute("SELECT * FROM voice_sessions WHERE id = ?", (session_id,)).fetchone()
            if row:
                return dict(row)
            now = _utc_now_iso()
            conn.execute(
                """
                INSERT INTO voice_sessions
                    (id, language, turn_count, ai_disclosed, pending_action_json, last_committed_tx_ids_json, updated_at)
                VALUES (?, ?, 0, 0, NULL, NULL, ?)
                """,
                (session_id, language, now),
            )
            return {
                "id": session_id,
                "language": language,
                "turn_count": 0,
                "ai_disclosed": 0,
                "pending_action_json": None,
                "last_committed_tx_ids_json": None,
                "updated_at": now,
            }

    def get_pending_action(self, session_id: str) -> dict | None:
        with self._conn() as conn:
            row = conn.execute("SELECT pending_action_json FROM voice_sessions WHERE id = ?", (session_id,)).fetchone()
            if row and row["pending_action_json"]:
                return json.loads(row["pending_action_json"])
            return None

    def update_session(
        self,
        session_id: str,
        *,
        language: str,
        pending_action: dict | None,
        last_committed_tx_ids: list[int],
        updated_at: str,
    ) -> None:
        with self._conn(write=True) as conn:
            conn.execute(
                """
                UPDATE voice_sessions
                SET language = ?, turn_count = turn_count + 1, ai_disclosed = 1,
                    pending_action_json = ?, last_committed_tx_ids_json = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    language,
                    json.dumps(pending_action) if pending_action else None,
                    json.dumps(last_committed_tx_ids) if last_committed_tx_ids else None,
                    updated_at,
                    session_id,
                ),
            )

    def insert_voice_log(
        self,
        *,
        session_id: str,
        channel: str,
        utterance_enc: str | None,
        parsed_json: str,
        ai_reply_enc: str | None,
        model_used: str,
        latency_ms: int,
        created_at: str,
    ) -> int:
        with self._conn(write=True) as conn:
            cur = conn.execute(
                """
                INSERT INTO voice_logs
                    (session_id, channel, utterance_enc, parsed_json, ai_reply_enc, model_used, latency_ms, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (session_id, channel, utterance_enc, parsed_json, ai_reply_enc, model_used, latency_ms, created_at),
            )
            return int(cur.lastrowid)

    def update_voice_log_latency(self, voice_log_id: int, total_ms: int) -> None:
        with self._conn(write=True) as conn:
            conn.execute(
                "UPDATE voice_logs SET latency_ms = ? WHERE id = ?",
                (max(0, int(total_ms)), voice_log_id),
            )

    def list_voice_history(self, limit: int = 40) -> list[dict]:
        capped = min(max(limit, 1), 200)
        with self._conn() as conn:
            rows = conn.execute(
                """
                SELECT id, session_id, channel, utterance_enc, parsed_json, ai_reply_enc, model_used, latency_ms, created_at
                FROM voice_logs
                ORDER BY id DESC
                LIMIT ?
                """,
                (capped,),
            ).fetchall()
            return [dict(r) for r in rows]

    def ensure_default_settings(self, defaults: list[tuple[str, str]]) -> None:
        with self._conn(write=True) as conn:
            conn.executemany(
                "INSERT OR IGNORE INTO app_settings (key, value) VALUES (?, ?)",
                defaults,
            )

    def get_all_settings(self) -> dict[str, str]:
        with self._conn() as conn:
            rows = conn.execute("SELECT key, value FROM app_settings").fetchall()
            return {r["key"]: r["value"] for r in rows}

    def get_setting(self, key: str, default: str) -> str:
        with self._conn() as conn:
            row = conn.execute("SELECT value FROM app_settings WHERE key = ?", (key,)).fetchone()
            return row["value"] if row else default

    def upsert_settings(self, updates: dict[str, str]) -> dict[str, str]:
        with self._conn(write=True) as conn:
            for k, v in updates.items():
                conn.execute(
                    "INSERT INTO app_settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                    (k, v),
                )
            rows = conn.execute("SELECT key, value FROM app_settings").fetchall()
            return {r["key"]: r["value"] for r in rows}

    def wipe_demo_transcripts(self) -> int:
        with self._conn(write=True) as conn:
            cur = conn.execute("DELETE FROM voice_logs")
            deleted = cur.rowcount
            conn.execute("UPDATE transactions SET raw_utterance_enc = NULL WHERE raw_utterance_enc IS NOT NULL")
            conn.execute("UPDATE voice_sessions SET pending_action_json = NULL, last_committed_tx_ids_json = NULL")
            return deleted


def _resolve_mongo_db_name(mongo_url: str, configured_db_name: str) -> str:
    """Use configured DB name unless URL specifies a custom path DB and config is untouched."""
    parsed_path = urlparse(mongo_url).path.strip("/")
    if parsed_path and configured_db_name == "voice_khata":
        return parsed_path.split("/")[0]
    return configured_db_name or "voice_khata"


class MongoKhataStore:
    """MongoDB-backed store using atomic integer sequences to keep API schemas unchanged."""

    backend_name = "mongodb"

    def __init__(
        self,
        mongo_url: str = "",
        db_name: str = "voice_khata",
        *,
        client: Any = None,
    ) -> None:
        self._owns_client = client is None
        self.client = client or MongoClient(mongo_url, serverSelectionTimeoutMS=5000)
        resolved_name = _resolve_mongo_db_name(mongo_url, db_name)
        self.database_label = resolved_name
        self.mongo_db = self.client[resolved_name]
        self._ping_and_init_indexes()

    def _ping_and_init_indexes(self) -> None:
        self.client.admin.command("ping")
        self.mongo_db["customers"].create_index([("id", ASCENDING)], unique=True)
        self.mongo_db["customers"].create_index([("normalized_name", ASCENDING)])
        self.mongo_db["transactions"].create_index([("id", ASCENDING)], unique=True)
        self.mongo_db["transactions"].create_index([("customer_id", ASCENDING), ("status", ASCENDING)])
        self.mongo_db["transactions"].create_index([("transaction_date", ASCENDING), ("tx_type", ASCENDING), ("status", ASCENDING)])
        self.mongo_db["voice_sessions"].create_index([("id", ASCENDING)], unique=True)
        self.mongo_db["voice_logs"].create_index([("id", ASCENDING)], unique=True)
        self.mongo_db["voice_logs"].create_index([("created_at", DESCENDING)])
        self.mongo_db["audit_logs"].create_index([("id", ASCENDING)], unique=True)
        self.mongo_db["audit_logs"].create_index([("transaction_id", ASCENDING), ("created_at", DESCENDING)])
        self.mongo_db["app_settings"].create_index([("key", ASCENDING)], unique=True)

    def close(self) -> None:
        if self._owns_client and hasattr(self.client, "close"):
            self.client.close()

    @contextmanager
    def unit_of_work(self) -> Iterator[None]:
        # Standalone MongoDB deployments do not support multi-document transactions;
        # each repository write operates on single-document atomic operations.
        yield

    def _next_id(self, sequence_name: str) -> int:
        doc = self.mongo_db["counters"].find_one_and_update(
            {"_id": sequence_name},
            {"$inc": {"seq": 1}},
            upsert=True,
            return_document=ReturnDocument.AFTER,
        )
        return int(doc["seq"])

    def _customer_totals_map(self, customer_ids: list[int] | None = None) -> dict[int, tuple[float, float]]:
        query: dict[str, Any] = {"status": "confirmed"}
        if customer_ids is not None:
            query["customer_id"] = {"$in": customer_ids}
        totals: dict[int, list[float]] = {}
        for tx in self.mongo_db["transactions"].find(query, {"_id": 0, "customer_id": 1, "tx_type": 1, "amount": 1}):
            cid = int(tx["customer_id"])
            bucket = totals.setdefault(cid, [0.0, 0.0])
            amt = float(tx["amount"])
            if tx["tx_type"] == "credit":
                bucket[0] += amt
            elif tx["tx_type"] == "payment":
                bucket[1] += amt
        return {cid: (round(vals[0], 2), round(vals[1], 2)) for cid, vals in totals.items()}

    def _customer_name_map(self) -> dict[int, str]:
        return {
            int(c["id"]): c["name"]
            for c in self.mongo_db["customers"].find({}, {"_id": 0, "id": 1, "name": 1})
        }

    def count_customers(self) -> int:
        return int(self.mongo_db["customers"].count_documents({}))

    def list_customers_raw(self) -> list[dict]:
        docs = self.mongo_db["customers"].find({}, {"_id": 0}).sort("name", ASCENDING)
        return [_clean_doc(d) for d in docs if d is not None]

    def list_customer_names(self) -> list[str]:
        return [d["name"] for d in self.list_customers_raw()]

    def list_customers_with_totals(self, search_query: str = "") -> list[dict]:
        docs = self.list_customers_raw()
        if search_query.strip():
            needle = normalize_name(search_query)
            docs = [d for d in docs if needle in d.get("normalized_name", "")]
        if not docs:
            return []
        totals_map = self._customer_totals_map([int(d["id"]) for d in docs])
        for doc in docs:
            credit, payment = totals_map.get(int(doc["id"]), (0.0, 0.0))
            doc["total_credit"] = credit
            doc["total_payment"] = payment
        return docs

    def get_customer_by_id(self, customer_id: int) -> dict | None:
        return _clean_doc(self.mongo_db["customers"].find_one({"id": int(customer_id)}, {"_id": 0}))

    def get_customer_with_totals(self, customer_id: int) -> dict | None:
        doc = self.get_customer_by_id(customer_id)
        if not doc:
            return None
        credit, payment = self._customer_totals_map([int(customer_id)]).get(int(customer_id), (0.0, 0.0))
        doc["total_credit"] = credit
        doc["total_payment"] = payment
        return doc

    def create_customer(
        self,
        *,
        name: str,
        phone_enc: str | None,
        reminder_opt_in: bool,
        preferred_language: str,
        is_synthetic: bool = False,
        created_at: str | None = None,
    ) -> dict:
        now = created_at or _utc_now_iso()
        clean_name = name.strip()
        doc = {
            "id": self._next_id("customers"),
            "name": clean_name,
            "normalized_name": normalize_name(clean_name),
            "phone_enc": phone_enc,
            "reminder_opt_in": 1 if reminder_opt_in else 0,
            "preferred_language": preferred_language,
            "is_synthetic": 1 if is_synthetic else 0,
            "created_at": now,
        }
        self.mongo_db["customers"].insert_one(dict(doc))
        doc["total_credit"] = 0.0
        doc["total_payment"] = 0.0
        return doc

    def update_customer(
        self,
        customer_id: int,
        *,
        name: str,
        phone_enc: str | None,
        reminder_opt_in: bool,
        preferred_language: str,
    ) -> dict | None:
        clean_name = name.strip()
        res = self.mongo_db["customers"].update_one(
            {"id": int(customer_id)},
            {
                "$set": {
                    "name": clean_name,
                    "normalized_name": normalize_name(clean_name),
                    "phone_enc": phone_enc,
                    "reminder_opt_in": 1 if reminder_opt_in else 0,
                    "preferred_language": preferred_language,
                }
            },
        )
        if res.matched_count == 0:
            return None
        return self.get_customer_with_totals(customer_id)

    def delete_customer(self, customer_id: int) -> bool:
        res = self.mongo_db["customers"].delete_one({"id": int(customer_id)})
        if res.deleted_count == 0:
            return False
        self.mongo_db["transactions"].delete_many({"customer_id": int(customer_id)})
        return True

    def get_customer_balance(self, customer_id: int) -> float:
        credit, payment = self._customer_totals_map([int(customer_id)]).get(int(customer_id), (0.0, 0.0))
        return round(credit - payment, 2)

    def get_dashboard_summary(self, today_str: str, recent_limit: int = 12) -> dict:
        today_credit = 0.0
        today_payment = 0.0
        total_credit = 0.0
        total_payment = 0.0
        names = self._customer_name_map()
        today_credit_rows: list[dict[str, Any]] = []
        today_payment_rows: list[dict[str, Any]] = []
        cust_totals: dict[int, list[float]] = {}

        for tx in self.mongo_db["transactions"].find(
            {"status": "confirmed"},
            {"_id": 0, "customer_id": 1, "amount": 1, "tx_type": 1, "transaction_date": 1},
        ):
            amt = float(tx["amount"])
            cid = int(tx["customer_id"])
            cname = names.get(cid)
            bucket = cust_totals.setdefault(cid, [0.0, 0.0])
            if tx["tx_type"] == "credit":
                total_credit += amt
                bucket[0] += amt
                if tx["transaction_date"] == today_str:
                    today_credit += amt
                    if cname:
                        today_credit_rows.append({"customer_id": cid, "customer_name": cname, "amount": amt})
            elif tx["tx_type"] == "payment":
                total_payment += amt
                bucket[1] += amt
                if tx["transaction_date"] == today_str:
                    today_payment += amt
                    if cname:
                        today_payment_rows.append({"customer_id": cid, "customer_name": cname, "amount": amt})

        recent_docs = (
            self.mongo_db["transactions"]
            .find({"status": "confirmed"}, {"_id": 0})
            .sort("id", DESCENDING)
            .limit(recent_limit)
        )
        recent = []
        for raw in recent_docs:
            doc = _clean_doc(raw)
            if doc is not None and int(doc["customer_id"]) in names:
                doc["customer_name"] = names[int(doc["customer_id"])]
                recent.append(doc)

        outstanding_customers = []
        for cid, cname in names.items():
            cred, pay = cust_totals.get(cid, (0.0, 0.0))
            bal = round(cred - pay, 2)
            if bal > 0:
                outstanding_customers.append(
                    {"customer_id": cid, "customer_name": cname, "amount": bal, "tx_count": 1}
                )
        outstanding_customers.sort(key=lambda item: (-item["amount"], item["customer_name"].lower()))

        return {
            "today_credit": round(today_credit, 2),
            "today_payment": round(today_payment, 2),
            "total_outstanding": round(total_credit - total_payment, 2),
            "total_customers": self.count_customers(),
            "recent_transactions": recent,
            "today_credit_customers": group_transactions_by_customer(today_credit_rows),
            "today_payment_customers": group_transactions_by_customer(today_payment_rows),
            "outstanding_customers": outstanding_customers,
        }

    def list_customer_transactions(self, customer_id: int) -> list[dict]:
        cust = self.get_customer_by_id(customer_id)
        if not cust:
            return []
        docs = (
            self.mongo_db["transactions"]
            .find({"customer_id": int(customer_id), "status": {"$ne": "voided"}}, {"_id": 0})
            .sort([("transaction_date", DESCENDING), ("id", DESCENDING)])
        )
        out = []
        for raw in docs:
            doc = _clean_doc(raw)
            if doc is not None:
                doc["customer_name"] = cust["name"]
                out.append(doc)
        return out

    def get_customer_recent_confirmed_transactions(self, customer_id: int, limit: int = 3) -> list[dict]:
        docs = (
            self.mongo_db["transactions"]
            .find({"customer_id": int(customer_id), "status": "confirmed"}, {"_id": 0, "amount": 1, "tx_type": 1, "transaction_date": 1})
            .sort([("transaction_date", DESCENDING), ("id", DESCENDING)])
            .limit(limit)
        )
        return [_clean_doc(d) for d in docs if d is not None]

    def list_date_transactions(self, tx_type: str | None, target_date: str) -> list[dict]:
        filt: dict[str, Any] = {"status": "confirmed", "transaction_date": target_date}
        if tx_type in ("credit", "payment"):
            filt["tx_type"] = tx_type
        names = self._customer_name_map()
        docs = self.mongo_db["transactions"].find(filt, {"_id": 0}).sort("id", DESCENDING)
        out = []
        for raw in docs:
            doc = _clean_doc(raw)
            if doc is not None and int(doc["customer_id"]) in names:
                doc["customer_name"] = names[int(doc["customer_id"])]
                out.append(doc)
        return out

    def get_date_tx_sum(self, tx_type: str, target_date: str) -> float:
        cursor = self.mongo_db["transactions"].find(
            {"tx_type": tx_type, "status": "confirmed", "transaction_date": target_date},
            {"_id": 0, "amount": 1},
        )
        return float(sum(float(d["amount"]) for d in cursor))

    def get_total_outstanding(self) -> float:
        total_credit = 0.0
        total_payment = 0.0
        for tx in self.mongo_db["transactions"].find({"status": "confirmed"}, {"_id": 0, "amount": 1, "tx_type": 1}):
            amt = float(tx["amount"])
            if tx["tx_type"] == "credit":
                total_credit += amt
            elif tx["tx_type"] == "payment":
                total_payment += amt
        return round(total_credit - total_payment, 2)

    def get_top_debtor(self) -> tuple[str, float] | None:
        customers = self.list_customers_raw()
        if not customers:
            return None
        totals_map = self._customer_totals_map([int(c["id"]) for c in customers])
        best_name: str | None = None
        best_bal = float("-inf")
        for cust in customers:
            credit, payment = totals_map.get(int(cust["id"]), (0.0, 0.0))
            bal = credit - payment
            if bal > best_bal:
                best_bal = bal
                best_name = cust["name"]
        return (best_name, best_bal) if best_name is not None else None

    def get_transaction_with_customer(self, tx_id: int) -> dict | None:
        tx = _clean_doc(self.mongo_db["transactions"].find_one({"id": int(tx_id)}, {"_id": 0}))
        if not tx:
            return None
        cust = self.get_customer_by_id(int(tx["customer_id"]))
        if not cust:
            return None
        tx["customer_name"] = cust["name"]
        return tx

    def create_transaction(
        self,
        *,
        customer_id: int,
        customer_name: str,
        amount: float,
        tx_type: str,
        transaction_date: str,
        status: str = "confirmed",
        source_session_id: str | None = None,
        raw_utterance_enc: str | None = None,
        note: str | None = None,
        confidence: float = 1.0,
        audit_action: str = "create",
        audit_reason: str = "manual_entry",
        audit_new_value: dict[str, Any] | None = None,
        created_at: str | None = None,
    ) -> dict:
        now = created_at or _utc_now_iso()
        rounded = round(float(amount), 2)
        tx_id = self._next_id("transactions")
        tx_doc = {
            "id": tx_id,
            "customer_id": int(customer_id),
            "amount": rounded,
            "tx_type": tx_type,
            "transaction_date": transaction_date,
            "status": status,
            "source_session_id": source_session_id,
            "raw_utterance_enc": raw_utterance_enc,
            "note": note,
            "confidence": float(confidence),
            "created_at": now,
            "updated_at": now,
        }
        self.mongo_db["transactions"].insert_one(dict(tx_doc))

        audit_payload = audit_new_value or {
            "customer_id": int(customer_id),
            "amount": rounded,
            "tx_type": tx_type,
            "date": transaction_date,
        }
        audit_doc = {
            "id": self._next_id("audit_logs"),
            "transaction_id": tx_id,
            "action": audit_action,
            "old_value_json": None,
            "new_value_json": json.dumps(audit_payload),
            "reason": audit_reason,
            "created_at": now,
        }
        self.mongo_db["audit_logs"].insert_one(dict(audit_doc))

        return {
            "id": tx_id,
            "customer_id": int(customer_id),
            "customer_name": customer_name,
            "amount": rounded,
            "tx_type": tx_type,
            "transaction_date": transaction_date,
            "status": status,
            "note": note,
            "confidence": float(confidence),
            "created_at": now,
        }

    def correct_transaction(
        self,
        tx_id: int,
        *,
        new_amount: float,
        new_tx_type: str,
        old_amount: float,
        old_tx_type: str,
        reason: str,
        updated_at: str,
    ) -> None:
        self.mongo_db["transactions"].update_one(
            {"id": int(tx_id)},
            {"$set": {"amount": round(new_amount, 2), "tx_type": new_tx_type, "updated_at": updated_at}},
        )
        audit_doc = {
            "id": self._next_id("audit_logs"),
            "transaction_id": int(tx_id),
            "action": "correct",
            "old_value_json": json.dumps({"amount": old_amount, "tx_type": old_tx_type}),
            "new_value_json": json.dumps({"amount": new_amount, "tx_type": new_tx_type}),
            "reason": reason,
            "created_at": updated_at,
        }
        self.mongo_db["audit_logs"].insert_one(dict(audit_doc))

    def export_transactions(self, customer_id: int | None = None) -> list[dict]:
        query: dict[str, Any] = {}
        if customer_id is not None:
            query["customer_id"] = int(customer_id)
        names = self._customer_name_map()
        docs = (
            self.mongo_db["transactions"]
            .find(query, {"_id": 0})
            .sort([("transaction_date", DESCENDING), ("id", DESCENDING)])
        )
        out = []
        for raw in docs:
            doc = _clean_doc(raw)
            if doc is not None and int(doc["customer_id"]) in names:
                doc["customer_name"] = names[int(doc["customer_id"])]
                out.append(doc)
        return out

    def list_audit_logs(self, limit: int = 50) -> list[dict]:
        capped = min(max(limit, 1), 200)
        docs = self.mongo_db["audit_logs"].find({}, {"_id": 0}).sort("id", DESCENDING).limit(capped)
        return [_clean_doc(d) for d in docs if d is not None]

    def get_or_create_session(self, session_id: str, language: str) -> dict:
        existing = _clean_doc(self.mongo_db["voice_sessions"].find_one({"id": session_id}, {"_id": 0}))
        if existing:
            return existing
        now = _utc_now_iso()
        doc = {
            "id": session_id,
            "language": language,
            "turn_count": 0,
            "ai_disclosed": 0,
            "pending_action_json": None,
            "last_committed_tx_ids_json": None,
            "updated_at": now,
        }
        self.mongo_db["voice_sessions"].insert_one(dict(doc))
        return doc

    def get_pending_action(self, session_id: str) -> dict | None:
        doc = self.mongo_db["voice_sessions"].find_one({"id": session_id}, {"_id": 0, "pending_action_json": 1})
        if doc and doc.get("pending_action_json"):
            return json.loads(doc["pending_action_json"])
        return None

    def update_session(
        self,
        session_id: str,
        *,
        language: str,
        pending_action: dict | None,
        last_committed_tx_ids: list[int],
        updated_at: str,
    ) -> None:
        self.mongo_db["voice_sessions"].update_one(
            {"id": session_id},
            {
                "$set": {
                    "language": language,
                    "ai_disclosed": 1,
                    "pending_action_json": json.dumps(pending_action) if pending_action else None,
                    "last_committed_tx_ids_json": json.dumps(last_committed_tx_ids) if last_committed_tx_ids else None,
                    "updated_at": updated_at,
                },
                "$inc": {"turn_count": 1},
            },
        )

    def insert_voice_log(
        self,
        *,
        session_id: str,
        channel: str,
        utterance_enc: str | None,
        parsed_json: str,
        ai_reply_enc: str | None,
        model_used: str,
        latency_ms: int,
        created_at: str,
    ) -> int:
        log_id = self._next_id("voice_logs")
        doc = {
            "id": log_id,
            "session_id": session_id,
            "channel": channel,
            "utterance_enc": utterance_enc,
            "parsed_json": parsed_json,
            "ai_reply_enc": ai_reply_enc,
            "model_used": model_used,
            "latency_ms": int(latency_ms),
            "created_at": created_at,
        }
        self.mongo_db["voice_logs"].insert_one(dict(doc))
        return log_id

    def update_voice_log_latency(self, voice_log_id: int, total_ms: int) -> None:
        self.mongo_db["voice_logs"].update_one(
            {"id": int(voice_log_id)},
            {"$set": {"latency_ms": max(0, int(total_ms))}},
        )

    def list_voice_history(self, limit: int = 40) -> list[dict]:
        capped = min(max(limit, 1), 200)
        docs = self.mongo_db["voice_logs"].find({}, {"_id": 0}).sort("id", DESCENDING).limit(capped)
        return [_clean_doc(d) for d in docs if d is not None]

    def ensure_default_settings(self, defaults: list[tuple[str, str]]) -> None:
        for key, value in defaults:
            self.mongo_db["app_settings"].update_one(
                {"key": key},
                {"$setOnInsert": {"key": key, "value": value}},
                upsert=True,
            )

    def get_all_settings(self) -> dict[str, str]:
        docs = self.mongo_db["app_settings"].find({}, {"_id": 0, "key": 1, "value": 1})
        return {d["key"]: d["value"] for d in docs}

    def get_setting(self, key: str, default: str) -> str:
        doc = self.mongo_db["app_settings"].find_one({"key": key}, {"_id": 0, "value": 1})
        return doc["value"] if doc else default

    def upsert_settings(self, updates: dict[str, str]) -> dict[str, str]:
        for k, v in updates.items():
            self.mongo_db["app_settings"].update_one(
                {"key": k},
                {"$set": {"key": k, "value": v}},
                upsert=True,
            )
        return self.get_all_settings()

    def wipe_demo_transcripts(self) -> int:
        res = self.mongo_db["voice_logs"].delete_many({})
        self.mongo_db["transactions"].update_many(
            {"raw_utterance_enc": {"$ne": None}},
            {"$set": {"raw_utterance_enc": None}},
        )
        self.mongo_db["voice_sessions"].update_many(
            {},
            {"$set": {"pending_action_json": None, "last_committed_tx_ids_json": None}},
        )
        return int(res.deleted_count)

