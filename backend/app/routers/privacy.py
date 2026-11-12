"""Privacy, DPDP compliance, settings & AI provider status endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.deps import AppContainer, get_container
from app.schemas.models import SettingsUpdate
from app.services.responsible_ai import get_ai_disclosure, wipe_demo_transcripts


router = APIRouter(prefix="/api", tags=["privacy-settings"])


@router.get("/status")
def get_system_status(container: AppContainer = Depends(get_container)) -> dict:
    settings_rows = container.db.get_all_settings()
    return {
        "status": "ok",
        "gemini_configured": container.settings.has_gemini,
        "gnani_configured": container.gnani.is_available,
        "live_ws_supported": not container.settings.is_serverless,
        "database_backend": container.db.backend_name,
        "database_name": container.db.database_label,
        "storage_fallback_reason": container.storage_fallback_reason,
        "models": {
            "fast": container.settings.gemini_fast_model,
            "pro": container.settings.gemini_pro_model,
            "carbon": container.settings.gemini_carbon_model,
            "live": container.settings.gemini_live_model,
            "tts": container.settings.gemini_tts_model,
            "gnani_stt": container.settings.gnani_stt_model,
            "gnani_tts": container.settings.gnani_tts_model,
        },
        "encryption_at_rest": "AES-256-GCM",
        "settings": settings_rows,
        "ai_disclosure_preview": get_ai_disclosure(settings_rows.get("preferred_language", "hi-IN")),
    }


@router.get("/settings")
def get_settings_endpoint(container: AppContainer = Depends(get_container)) -> dict:
    return container.db.get_all_settings()


@router.patch("/settings")
def update_settings_endpoint(req: SettingsUpdate, container: AppContainer = Depends(get_container)) -> dict:
    updates: dict[str, str] = {}
    if req.preferred_language is not None:
        updates["preferred_language"] = req.preferred_language
    if req.ai_mode is not None:
        updates["ai_mode"] = req.ai_mode
    if req.speech_provider is not None:
        updates["speech_provider"] = req.speech_provider
    if req.ephemeral_transcripts is not None:
        updates["ephemeral_transcripts"] = "true" if req.ephemeral_transcripts else "false"
    if req.dpdp_consent_accepted is not None:
        updates["dpdp_consent_accepted"] = "true" if req.dpdp_consent_accepted else "false"

    return container.db.upsert_settings(updates)


@router.delete("/privacy/transcripts")
def delete_transcripts_dpdp(container: AppContainer = Depends(get_container)) -> dict:
    """DPDP Act compliance: delete all stored demo transcripts and session buffers immediately."""
    deleted = wipe_demo_transcripts(container.db)
    return {"deleted_voice_logs": deleted, "status": "wiped"}

