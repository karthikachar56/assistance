"""Application settings loaded from environment variables."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


ROOT_DIR = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    """Typed runtime configuration for Voice Khata."""

    model_config = SettingsConfigDict(
        env_file=str(ROOT_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Gemini AI
    gemini_api_key: str = ""
    gemini_api_version: str = "v1beta"
    gemini_fast_model: str = "gemini-3.8-flash"
    gemini_lite_model: str = "gemini-3.5-flash-lite"
    gemini_pro_model: str = "gemini-4-argon"
    gemini_carbon_model: str = "gemini-4-carbon"
    gemini_live_model: str = "gemini-3.8-live"
    gemini_live_extended_model: str = "gemini-3.8-live-extended-thinking"
    gemini_tts_model: str = "gemini-3.8-flash-lite-tts"
    gemini_voice_name: str = "Charon"

    # Optional Gnani AI (Prisma STT & Timbre TTS)
    gnani_api_key: str = ""
    gnani_stt_model: str = "prisma-v3.0"
    gnani_tts_model: str = "timbre-v3.0"
    gnani_tts_voice: str = "Nalini"

    # Optimization & Routing
    speech_provider: Literal["auto", "gemini", "gnani", "browser"] = "auto"
    ai_mode: Literal["cascaded", "flash", "argon", "carbon", "offline"] = "cascaded"
    escalation_confidence_threshold: float = Field(default=0.80, ge=0.0, le=1.0)

    # Security & DPDP Compliance
    ledger_encryption_key: str = ""
    ephemeral_transcripts: bool = False
    database_path: str = str(ROOT_DIR / "backend" / "data" / "voice_khata.db")
    mongodb_url: str = Field(
        default="",
        validation_alias=AliasChoices("mongodb_url", "MONGODB_URL", "MONGODB_URI"),
    )
    mongodb_db_name: str = "voice_khata"

    # Server
    backend_host: str = "0.0.0.0"
    backend_port: int = 8000
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"

    @property
    def has_mongo(self) -> bool:
        return bool(self.mongodb_url.strip())

    @property
    def has_gemini(self) -> bool:
        key = self.gemini_api_key.strip()
        return bool(key and key != "your_gemini_api_key_here")

    @property
    def has_gnani(self) -> bool:
        return bool(self.gnani_api_key.strip())

    @property
    def resolved_database_path(self) -> str:
        p = Path(self.database_path)
        return str(p if p.is_absolute() else (ROOT_DIR / p))

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]


@lru_cache
def get_settings() -> Settings:
    """Return cached settings instance."""
    return Settings()

