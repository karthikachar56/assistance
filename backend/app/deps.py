"""Application dependency container for clean service wiring and test isolation."""

from __future__ import annotations

from dataclasses import dataclass

from app.config import Settings, get_settings
from app.db.crypto import FieldCipher
from app.db.seed import seed_synthetic_data
from app.db.store import KhataStore, MongoKhataStore, SqliteKhataStore
from app.services.gemini_engine import GeminiKhataEngine
from app.services.gnani_adapter import GnaniSpeechAdapter
from app.services.ledger_engine import LedgerEngine


@dataclass
class AppContainer:
    settings: Settings
    db: KhataStore
    cipher: FieldCipher
    ledger: LedgerEngine
    gemini: GeminiKhataEngine
    gnani: GnaniSpeechAdapter


_container: AppContainer | None = None


def build_container(settings: Settings | None = None, db_path: str | None = None) -> AppContainer:
    cfg = settings or get_settings()
    if db_path is not None or not cfg.has_mongo:
        db: KhataStore = SqliteKhataStore(db_path=db_path or cfg.resolved_database_path)
    else:
        db = MongoKhataStore(mongo_url=cfg.mongodb_url.strip(), db_name=cfg.mongodb_db_name)
    cipher = FieldCipher(cfg.ledger_encryption_key or None)
    seed_synthetic_data(db, cipher)
    return AppContainer(
        settings=cfg,
        db=db,
        cipher=cipher,
        ledger=LedgerEngine(db, cipher),
        gemini=GeminiKhataEngine(cfg),
        gnani=GnaniSpeechAdapter(cfg),
    )


def get_container() -> AppContainer:
    global _container
    if _container is None:
        _container = build_container()
    return _container


def set_container(container: AppContainer | None) -> None:
    global _container
    _container = container

