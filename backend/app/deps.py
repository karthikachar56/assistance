"""Application dependency container for clean service wiring and test isolation."""

from __future__ import annotations

from dataclasses import dataclass
import logging
import re

from app.config import Settings, get_settings
from app.db.crypto import FieldCipher
from app.db.seed import seed_synthetic_data
from app.db.store import KhataStore, MongoKhataStore, SqliteKhataStore
from app.services.gemini_engine import GeminiKhataEngine
from app.services.gnani_adapter import GnaniSpeechAdapter
from app.services.ledger_engine import LedgerEngine

logger = logging.getLogger(__name__)
_CRED_URI_RE = re.compile(r"(mongodb(?:\+srv)?://)([^/@\s]+@)", re.IGNORECASE)


def _format_mongo_fallback_reason(exc: Exception) -> str:
    """Summarize Mongo connection failures safely without exposing secrets or URIs."""
    raw = _CRED_URI_RE.sub(r"\1***@", f"{type(exc).__name__}: {exc}")
    if "TLSV1_ALERT_INTERNAL_ERROR" in raw or "SSL handshake failed" in raw:
        return (
            "MongoDB Atlas SSL handshake failed (allow 0.0.0.0/0 in Atlas Network Access for Vercel). "
            "Running on SQLite fallback."
        )
    return f"MongoDB unreachable ({raw[:180]}). Running on SQLite fallback."


@dataclass
class AppContainer:
    settings: Settings
    db: KhataStore
    cipher: FieldCipher
    ledger: LedgerEngine
    gemini: GeminiKhataEngine
    gnani: GnaniSpeechAdapter
    storage_fallback_reason: str | None = None


_container: AppContainer | None = None


def build_container(settings: Settings | None = None, db_path: str | None = None) -> AppContainer:
    cfg = settings or get_settings()
    cipher = FieldCipher(cfg.ledger_encryption_key or None)
    storage_fallback_reason: str | None = None
    mongo_url = cfg.mongodb_url.strip().strip('"').strip("'")

    if db_path is not None or not mongo_url:
        db: KhataStore = SqliteKhataStore(db_path=db_path or cfg.resolved_database_path)
        seed_synthetic_data(db, cipher)
    else:
        try:
            db = MongoKhataStore(mongo_url=mongo_url, db_name=cfg.mongodb_db_name)
            seed_synthetic_data(db, cipher)
        except Exception as exc:
            storage_fallback_reason = _format_mongo_fallback_reason(exc)
            logger.warning("%s", storage_fallback_reason)
            db = SqliteKhataStore(db_path=cfg.resolved_database_path)
            seed_synthetic_data(db, cipher)

    return AppContainer(
        settings=cfg,
        db=db,
        cipher=cipher,
        ledger=LedgerEngine(db, cipher),
        gemini=GeminiKhataEngine(cfg),
        gnani=GnaniSpeechAdapter(cfg),
        storage_fallback_reason=storage_fallback_reason,
    )


def get_container() -> AppContainer:
    global _container
    if _container is None:
        _container = build_container()
    return _container


def set_container(container: AppContainer | None) -> None:
    global _container
    _container = container

