"""Voice & Gemini Live API endpoints with sub-2s fast-path and telemetry."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, WebSocket

from app.deps import AppContainer, get_container
from app.schemas.models import (
    LanguageCode,
    LatencyBreakdown,
    LiveTokenRequest,
    LiveToolExecuteRequest,
    TTSRequest,
    VoiceTurnPlan,
    VoiceTurnResponse,
    VoiceTurnTextRequest,
)
from app.services.fast_path import try_fast_path, verify_and_guard_plan
from app.services.gemini_live import run_gemini_live_bridge
from app.services.live_token_service import execute_browser_live_tool, mint_live_ephemeral_token


router = APIRouter(prefix="/api/voice", tags=["voice"])
ws_router = APIRouter(tags=["voice-live"])


def _read_setting(container: AppContainer, key: str, default: str) -> str:
    return container.db.get_setting(key, default)


def _maybe_synthesize_tts(
    container: AppContainer, text: str, language: LanguageCode
) -> tuple[str | None, str | None, int]:
    t0 = time.perf_counter()
    provider = _read_setting(container, "speech_provider", container.settings.speech_provider)
    try:
        if provider in ("gnani", "auto") and container.gnani.is_available:
            b64, mime = container.gnani.synthesize_speech(text, language)
            return b64, mime, int((time.perf_counter() - t0) * 1000)
        # Synthesize Gemini 3.8 Cloud WAV audio for both "gemini" and "auto" (only "browser" skips Cloud TTS)
        if provider in ("gemini", "auto") and container.settings.has_gemini:
            res = container.gemini.synthesize_tts(text, language)
            if res:
                return res[0], res[1], int((time.perf_counter() - t0) * 1000)
    except Exception:
        # Fail-soft to browser speechSynthesis so a TTS hiccup never breaks ledger confirmation
        return None, None, int((time.perf_counter() - t0) * 1000)
    return None, None, 0


def _parse_optional_json(raw: str) -> Any:
    if not raw or not raw.strip():
        return None
    try:
        return json.loads(raw)
    except Exception:
        return None


@router.post("/turn", response_model=VoiceTurnResponse)
def process_text_voice_turn(
    req: VoiceTurnTextRequest,
    container: AppContainer = Depends(get_container),
) -> VoiceTurnResponse:
    """Process a spoken transcript (from browser STT or test input) using Fast-Path + Gemini Cascade."""
    t_start = time.perf_counter()
    ai_mode = req.ai_mode or _read_setting(container, "ai_mode", container.settings.ai_mode)
    ephemeral = _read_setting(container, "ephemeral_transcripts", "false") == "true"

    server_pending = container.ledger.get_pending_action(req.session_id)
    pending = server_pending if server_pending is not None else req.pending_action
    known_names = container.ledger.list_customer_names()

    t_fp0 = time.perf_counter()
    fp_candidate = try_fast_path(req.transcript, req.language, pending, known_names)
    plan: VoiceTurnPlan | None = None
    model_used = "deterministic-fast-path"
    if ai_mode not in ("argon", "carbon"):
        plan = fp_candidate
    elif fp_candidate is not None and fp_candidate.intent in (
        "confirmation_reply",
        "disambiguation_reply",
        "delete_customer_blocked",
        "greeting",
    ):
        # Always honor deterministic UI confirm/cancel/disambiguation/safety replies instantly
        plan = fp_candidate
    fast_path_ms = int((time.perf_counter() - t_fp0) * 1000)

    ai_ms = 0
    if plan is None:
        if container.settings.has_gemini and ai_mode != "offline":
            t_ai0 = time.perf_counter()
            try:
                plan, model_used = container.gemini.parse_turn(
                    language=req.language,
                    known_customers=known_names,
                    pending_action=pending,
                    ai_mode=ai_mode,
                    transcript=req.transcript,
                )
                plan = verify_and_guard_plan(plan)
                if plan.intent == "unknown" and fp_candidate is not None:
                    plan = fp_candidate
                    model_used = f"{model_used} -> fast-path-rescue"
            except Exception as exc:
                plan = fp_candidate or VoiceTurnPlan(
                    transcript=req.transcript,
                    intent="unknown",
                    confidence=0.3,
                    detected_language=req.language,
                    clarification_question=f"Network/AI fallback active ({type(exc).__name__}). Please repeat clearly or check .env key.",
                )
                model_used = "offline-fallback"
            ai_ms = int((time.perf_counter() - t_ai0) * 1000)
        else:
            # Local deterministic fallback when offline or no GEMINI_API_KEY is configured yet
            plan = fp_candidate or VoiceTurnPlan(
                transcript=req.transcript,
                intent="unknown",
                confidence=0.3,
                detected_language=req.language,
            )
            model_used = "offline-deterministic"

    t_led0 = time.perf_counter()
    latency = LatencyBreakdown(fast_path_ms=fast_path_ms, ai_ms=ai_ms)
    res = container.ledger.process_turn(
        session_id=req.session_id,
        plan=plan,
        language=req.language,
        model_used=model_used,
        latency=latency,
        ephemeral_transcripts=ephemeral,
        channel="voice_text",
        interactive_confirm=req.interactive_confirm,
        fallback_pending_action=req.pending_action,
        fallback_last_tx_ids=req.last_committed_tx_ids,
    )
    ledger_ms = int((time.perf_counter() - t_led0) * 1000)

    tts_ms = 0
    if req.include_tts:
        b64, mime, tts_ms = _maybe_synthesize_tts(container, res.spoken_reply, req.language)
        res.audio_base64 = b64
        res.audio_mime_type = mime

    total_ms = int((time.perf_counter() - t_start) * 1000)
    res.latency = LatencyBreakdown(
        stt_ms=0,
        fast_path_ms=fast_path_ms,
        ai_ms=ai_ms,
        ledger_ms=ledger_ms,
        tts_ms=tts_ms,
        total_ms=total_ms,
    )
    container.ledger.update_voice_log_latency(res.voice_log_id, total_ms)
    return res


@router.post("/audio", response_model=VoiceTurnResponse)
async def process_audio_turn(
    file: UploadFile = File(...),
    session_id: str = Form(...),
    language: LanguageCode = Form("hi-IN"),
    ai_mode: str = Form("cascaded"),
    include_tts: bool = Form(True),
    fallback_transcript: str = Form(""),
    interactive_confirm: bool = Form(False),
    pending_action_json: str = Form(""),
    last_committed_tx_ids_json: str = Form(""),
    container: AppContainer = Depends(get_container),
) -> VoiceTurnResponse:
    """Process raw noisy shop audio via Gnani Prisma, Gemini Multimodal, or fail-soft fast-path fallback."""
    t_start = time.perf_counter()
    audio_bytes = await file.read()
    if not audio_bytes:
        raise HTTPException(status_code=400, detail="Empty audio payload.")

    mime_type = file.content_type or "audio/wav"
    suffix = Path(file.filename or "clip.wav").suffix or ".wav"
    provider = _read_setting(container, "speech_provider", container.settings.speech_provider)
    ephemeral = _read_setting(container, "ephemeral_transcripts", "false") == "true"

    parsed_pending = _parse_optional_json(pending_action_json)
    fallback_pending = parsed_pending if isinstance(parsed_pending, dict) else None
    parsed_tx_ids = _parse_optional_json(last_committed_tx_ids_json)
    fallback_tx_ids = (
        [int(x) for x in parsed_tx_ids if isinstance(x, (int, float))]
        if isinstance(parsed_tx_ids, list)
        else None
    )

    server_pending = container.ledger.get_pending_action(session_id)
    pending = server_pending if server_pending is not None else fallback_pending
    known_names = container.ledger.list_customer_names()

    stt_ms = 0
    fast_path_ms = 0
    ai_ms = 0
    plan: VoiceTurnPlan | None = None
    model_used = ""

    # Path 0: Sub-20ms hybrid boost when Push-to-Talk also captured a clear browser transcript
    clean_fallback = fallback_transcript.strip()
    fp_fallback_plan = (
        try_fast_path(clean_fallback, language, pending, known_names) if clean_fallback else None
    )
    if clean_fallback and ai_mode in ("cascaded", "flash"):
        t_fp0 = time.perf_counter()
        fp_plan = fp_fallback_plan
        fast_path_ms = int((time.perf_counter() - t_fp0) * 1000)
        if fp_plan is not None and fp_plan.confidence >= 0.90:
            plan = fp_plan
            model_used = "browser-stt + fast-path"

    # Path 1: Gnani Prisma STT -> Fast-Path -> Gemini Cascade
    if plan is None and (provider == "gnani" or (provider == "auto" and container.gnani.is_available)):
        t_stt0 = time.perf_counter()
        try:
            transcript = container.gnani.transcribe_audio(audio_bytes, language=language, suffix=suffix)
            stt_ms = int((time.perf_counter() - t_stt0) * 1000)

            t_fp0 = time.perf_counter()
            plan = try_fast_path(transcript, language, pending, known_names)
            fast_path_ms = int((time.perf_counter() - t_fp0) * 1000)
            if plan is not None:
                model_used = f"{container.settings.gnani_stt_model} + fast-path"
            elif container.settings.has_gemini:
                t_ai0 = time.perf_counter()
                plan, parse_model = container.gemini.parse_turn(
                    language=language,
                    known_customers=known_names,
                    pending_action=pending,
                    ai_mode=ai_mode,
                    transcript=transcript,
                )
                plan = verify_and_guard_plan(plan)
                model_used = f"{container.settings.gnani_stt_model} + {parse_model}"
                ai_ms = int((time.perf_counter() - t_ai0) * 1000)
        except Exception:
            plan = None

    # Path 2 (Primary Optimized): Gemini Direct Multimodal Audio -> resilient model cascade -> browser transcript fallback
    if plan is None:
        if not container.settings.has_gemini and not fallback_transcript.strip():
            raise HTTPException(
                status_code=503,
                detail="Audio endpoint requires GEMINI_API_KEY or GNANI_API_KEY. Browser Web Speech works without keys.",
            )
        if container.settings.has_gemini and ai_mode != "offline":
            t_ai0 = time.perf_counter()
            try:
                plan, model_used = container.gemini.parse_turn(
                    language=language,
                    known_customers=known_names,
                    pending_action=pending,
                    ai_mode=ai_mode,
                    audio_bytes=audio_bytes,
                    audio_mime_type=mime_type,
                )
                plan = verify_and_guard_plan(plan)
                if plan.intent == "unknown" and fp_fallback_plan is not None:
                    plan = fp_fallback_plan
                    model_used = f"{model_used} -> browser-stt-rescue"
            except Exception as exc:
                t_fp0 = time.perf_counter()
                if fp_fallback_plan is not None:
                    plan = fp_fallback_plan
                fast_path_ms = int((time.perf_counter() - t_fp0) * 1000)
                if plan is not None:
                    model_used = f"browser-stt + fast-path ({type(exc).__name__} fallback)"
                else:
                    plan = VoiceTurnPlan(
                        transcript=clean_fallback or "[Audio capture]",
                        intent="unknown",
                        confidence=0.25,
                        detected_language=language,
                        clarification_question=(
                            f"Temporary cloud spike ({type(exc).__name__}). Please repeat clearly or type below."
                        ),
                    )
                    model_used = f"cloud-fallback ({type(exc).__name__})"
            ai_ms = int((time.perf_counter() - t_ai0) * 1000)
        elif clean_fallback:
            t_fp0 = time.perf_counter()
            plan = fp_fallback_plan or VoiceTurnPlan(
                transcript=clean_fallback,
                intent="unknown",
                confidence=0.3,
                detected_language=language,
            )
            fast_path_ms = int((time.perf_counter() - t_fp0) * 1000)
            model_used = "offline-browser-stt"

        if plan is None:
            plan = VoiceTurnPlan(
                transcript=clean_fallback or "[Audio capture]",
                intent="unknown",
                confidence=0.25,
                detected_language=language,
                clarification_question="Switch AI Mode from Offline or enable Browser Speech to process audio.",
            )
            model_used = model_used or "offline-fallback"

    t_led0 = time.perf_counter()
    res = container.ledger.process_turn(
        session_id=session_id,
        plan=plan,
        language=language,
        model_used=model_used,
        latency=LatencyBreakdown(stt_ms=stt_ms, fast_path_ms=fast_path_ms, ai_ms=ai_ms),
        ephemeral_transcripts=ephemeral,
        channel="voice_audio",
        interactive_confirm=interactive_confirm,
        fallback_pending_action=fallback_pending,
        fallback_last_tx_ids=fallback_tx_ids,
    )
    ledger_ms = int((time.perf_counter() - t_led0) * 1000)

    tts_ms = 0
    if include_tts:
        b64, mime, tts_ms = _maybe_synthesize_tts(container, res.spoken_reply, language)
        res.audio_base64 = b64
        res.audio_mime_type = mime

    total_ms = int((time.perf_counter() - t_start) * 1000)
    res.latency = LatencyBreakdown(
        stt_ms=stt_ms,
        fast_path_ms=fast_path_ms,
        ai_ms=ai_ms,
        ledger_ms=ledger_ms,
        tts_ms=tts_ms,
        total_ms=total_ms,
    )
    container.ledger.update_voice_log_latency(res.voice_log_id, total_ms)
    return res


@router.post("/tts")
def synthesize_tts_endpoint(
    req: TTSRequest,
    container: AppContainer = Depends(get_container),
) -> dict:
    b64, mime, tts_ms = _maybe_synthesize_tts(container, req.text, req.language)
    return {"audio_base64": b64, "audio_mime_type": mime, "tts_ms": tts_ms}


@router.post("/live-token")
def create_live_ephemeral_token(
    req: LiveTokenRequest,
    container: AppContainer = Depends(get_container),
) -> dict:
    """Provision a short-lived ephemeral token for direct browser -> Gemini 3.8 Live WebSocket."""
    if not container.settings.has_gemini:
        raise HTTPException(status_code=503, detail="GEMINI_API_KEY is required for Live Voice Mode.")
    try:
        return mint_live_ephemeral_token(
            ledger=container.ledger,
            settings=container.settings,
            session_id=req.session_id,
            language=req.language,
            extended_thinking=req.extended,
            interactive_confirm=req.interactive_confirm,
            voice_name=req.voice,
            resume_handle=req.resume_handle,
        )
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Failed to start Gemini Live token ({type(exc).__name__}): {exc}",
        ) from exc


@router.post("/live-tool", response_model=VoiceTurnResponse)
def execute_live_tool_endpoint(
    req: LiveToolExecuteRequest,
    container: AppContainer = Depends(get_container),
) -> VoiceTurnResponse:
    """Execute a Gemini Live tool call against ACID SQLite/MongoDB storage over REST."""
    ephemeral = _read_setting(container, "ephemeral_transcripts", "false") == "true"
    return execute_browser_live_tool(
        ledger=container.ledger,
        session_id=req.session_id,
        language=req.language,
        model_used=req.model,
        tool_name=req.tool_name,
        args=req.args,
        ephemeral_transcripts=ephemeral,
        interactive_confirm=req.interactive_confirm,
        fallback_pending_action=req.pending_action,
        fallback_last_tx_ids=req.last_committed_tx_ids,
    )


@router.get("/history")
def get_voice_history(limit: int = 40, container: AppContainer = Depends(get_container)) -> list[dict]:
    rows = container.db.list_voice_history(limit=limit)
    out = []
    for r in rows:
        out.append(
            {
                "id": r["id"],
                "session_id": r["session_id"],
                "channel": r["channel"],
                "utterance": container.cipher.decrypt(r["utterance_enc"]),
                "parsed": json.loads(r["parsed_json"]) if r["parsed_json"] else None,
                "ai_reply": container.cipher.decrypt(r["ai_reply_enc"]),
                "model_used": r["model_used"],
                "latency_ms": r["latency_ms"],
                "created_at": r["created_at"],
            }
        )
    return out


@ws_router.websocket("/ws/live")
async def gemini_live_websocket(
    websocket: WebSocket,
    session_id: str = "live-session",
    language: str = "hi-IN",
    resume_handle: str | None = None,
    extended: bool = False,
    interactive_confirm: bool = False,
    greet: bool = False,
    voice: str | None = None,
    container: AppContainer = Depends(get_container),
) -> None:
    """Real-time Gemini 3.8 Live bidirectional audio session."""
    ephemeral = _read_setting(container, "ephemeral_transcripts", "false") == "true"
    await run_gemini_live_bridge(
        websocket=websocket,
        ledger=container.ledger,
        session_id=session_id,
        language=language,
        settings=container.settings,
        resume_handle=resume_handle,
        extended_thinking=extended,
        ephemeral_transcripts=ephemeral,
        interactive_confirm=interactive_confirm,
        greet=greet,
        voice_name=voice,
    )

