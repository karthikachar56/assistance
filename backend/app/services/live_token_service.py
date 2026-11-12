"""Ephemeral token provisioning & stateless tool execution for browser-direct Gemini 3.8 Live."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from typing import Any

from app.config import Settings
from app.schemas.models import LanguageCode, VoiceTurnResponse
from app.services.gemini_live import _build_live_tools, _execute_live_tool
from app.services.ledger_engine import LedgerEngine
from app.services.responsible_ai import get_ai_disclosure
from app.services.training_examples import build_kirana_system_prompt


def mint_live_ephemeral_token(
    *,
    ledger: LedgerEngine,
    settings: Settings,
    session_id: str,
    language: LanguageCode = "hi-IN",
    extended_thinking: bool = False,
    interactive_confirm: bool = False,
    voice_name: str | None = None,
    resume_handle: str | None = None,
) -> dict[str, Any]:
    """Mint a short-lived v1alpha ephemeral auth token and return the exact Gemini Live setup payload."""
    from google import genai

    client = genai.Client(
        api_key=settings.gemini_api_key,
        http_options={"api_version": "v1alpha"},
    )
    now = datetime.now(tz=timezone.utc)
    token = client.auth_tokens.create(
        config={
            "uses": 1,
            "expire_time": now + timedelta(minutes=25),
            "new_session_expire_time": now + timedelta(minutes=2),
        }
    )

    known_customers = ledger.list_customer_names()
    pending_action = ledger.get_pending_action(session_id)
    disclosure = get_ai_disclosure(language)
    live_model = settings.gemini_live_extended_model if extended_thinking else settings.gemini_live_model
    selected_voice = voice_name or settings.gemini_voice_name

    system_text = build_kirana_system_prompt(
        language=language,
        known_customers=known_customers,
        pending_action=pending_action,
        disclosure_text=disclosure,
        is_live=True,
        interactive_confirm=interactive_confirm,
    )
    tools = _build_live_tools(extended_thinking=extended_thinking)
    tools_json = [t.model_dump(by_alias=True, exclude_none=True, mode="json") for t in tools]

    generation_config: dict[str, Any] = {
        "responseModalities": ["AUDIO"],
        "speechConfig": {
            "voiceConfig": {
                "prebuiltVoiceConfig": {"voiceName": selected_voice},
            }
        },
    }
    if extended_thinking:
        generation_config["thinkingConfig"] = {"thinkingLevel": "low"}

    setup_payload: dict[str, Any] = {
        "model": f"models/{live_model}",
        "generationConfig": generation_config,
        "systemInstruction": {"parts": [{"text": system_text}]},
        "tools": tools_json,
        "inputAudioTranscription": {},
        "outputAudioTranscription": {},
        "realtimeInputConfig": {
            "automaticActivityDetection": {
                "startOfSpeechSensitivity": "START_SENSITIVITY_HIGH",
                "endOfSpeechSensitivity": "END_SENSITIVITY_HIGH",
                "prefixPaddingMs": 220,
                "silenceDurationMs": 650,
            }
        },
        "contextWindowCompression": {"slidingWindow": {}},
    }
    if resume_handle:
        setup_payload["sessionResumption"] = {"handle": resume_handle}
    else:
        setup_payload["sessionResumption"] = {}

    greet_phrase = (
        "ನಿಮ್ಮ ಸೇವೆಗೆ ಸಿದ್ಧವಾಗಿದೆ, ಸರ್."
        if language == "kn-IN"
        else "At your service, sir."
    )
    greet_prompt = (
        f'Begin the session now: speak this mandatory first-turn disclosure once clearly: "{disclosure}" '
        f'Then add a crisp greeting: "{greet_phrase}"'
    )

    ws_url = (
        "wss://generativelanguage.googleapis.com/ws/"
        f"google.ai.generativelanguage.v1beta.GenerativeService.BidiGenerateContentConstrained?access_token={token.name}"
    )

    return {
        "token": token.name,
        "ws_url": ws_url,
        "model": live_model,
        "voice": selected_voice,
        "language": language,
        "disclosure": disclosure,
        "greet_prompt": greet_prompt,
        "setup": setup_payload,
    }


def execute_browser_live_tool(
    *,
    ledger: LedgerEngine,
    session_id: str,
    language: LanguageCode,
    model_used: str,
    tool_name: str,
    args: dict[str, Any],
    ephemeral_transcripts: bool = False,
    interactive_confirm: bool = False,
    fallback_pending_action: dict | None = None,
    fallback_last_tx_ids: list[int] | None = None,
) -> VoiceTurnResponse:
    """Execute a Live tool call over HTTP while preserving stateless pending/last-tx state."""
    sess = ledger.db.get_or_create_session(session_id, language)
    existing_pending = json.loads(sess["pending_action_json"]) if sess.get("pending_action_json") else None
    existing_tx_ids = json.loads(sess["last_committed_tx_ids_json"]) if sess.get("last_committed_tx_ids_json") else []

    if (existing_pending is None and fallback_pending_action is not None) or (
        not existing_tx_ids and fallback_last_tx_ids
    ):
        ledger.db.update_session(
            session_id,
            language=language,
            pending_action=existing_pending if existing_pending is not None else fallback_pending_action,
            last_committed_tx_ids=existing_tx_ids or (fallback_last_tx_ids or []),
            updated_at=datetime.now(tz=timezone.utc).isoformat(),
        )

    return _execute_live_tool(
        ledger=ledger,
        session_id=session_id,
        language=language,
        model_used=model_used,
        tool_name=tool_name,
        args=args,
        ephemeral_transcripts=ephemeral_transcripts,
        interactive_confirm=interactive_confirm,
    )
