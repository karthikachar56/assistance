"""Gemini Live API (gemini-3.8-live) bidirectional audio bridge with v1beta, VAD tuning, and ACID ledger tools."""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import struct
import time
from typing import Any

from fastapi import WebSocket, WebSocketDisconnect

logger = logging.getLogger("uvicorn.error")

from app.config import Settings, get_settings
from app.schemas.models import (
    ExtractedOperation,
    ExtractedQuery,
    LatencyBreakdown,
    VoiceTurnPlan,
)
from app.services.ledger_engine import LedgerEngine
from app.services.responsible_ai import get_ai_disclosure
from app.services.training_examples import build_kirana_system_prompt


def _build_live_tools(extended_thinking: bool = False):
    from google.genai import types

    # Gemini 3.8 Live supports BLOCKING (ensures SQLite disambiguation/balance resolves before AI speaks);
    # Gemini 3.8 Live Extended Thinking requires NON_BLOCKING async tool declarations.
    behavior = types.Behavior.NON_BLOCKING if extended_thinking else types.Behavior.BLOCKING

    record_fn = types.FunctionDeclaration(
        name="record_khata_operations",
        description="Record one or more customer credit (udhaar) or payment transactions deterministically.",
        behavior=behavior,
        parameters=types.Schema(
            type=types.Type.OBJECT,
            properties={
                "transcript": types.Schema(type=types.Type.STRING),
                "operations": types.Schema(
                    type=types.Type.ARRAY,
                    items=types.Schema(
                        type=types.Type.OBJECT,
                        properties={
                            "customer_name": types.Schema(type=types.Type.STRING),
                            "amount": types.Schema(type=types.Type.NUMBER),
                            "tx_type": types.Schema(type=types.Type.STRING, enum=["credit", "payment"]),
                            "date_phrase": types.Schema(type=types.Type.STRING),
                            "direction_ambiguous": types.Schema(type=types.Type.BOOLEAN),
                        },
                        required=["customer_name", "amount", "tx_type"],
                    ),
                ),
            },
            required=["transcript", "operations"],
        ),
    )
    correct_fn = types.FunctionDeclaration(
        name="apply_spoken_correction",
        description="Correct the amount or type (credit vs payment) of the active pending or most recent transaction.",
        behavior=behavior,
        parameters=types.Schema(
            type=types.Type.OBJECT,
            properties={
                "transcript": types.Schema(type=types.Type.STRING),
                "corrected_amount": types.Schema(type=types.Type.NUMBER),
                "corrected_tx_type": types.Schema(type=types.Type.STRING, enum=["credit", "payment"]),
            },
            required=["transcript"],
        ),
    )
    resolve_fn = types.FunctionDeclaration(
        name="resolve_pending_customer",
        description="Select a specific customer when asked which duplicate customer was meant (e.g. Ramesh Kumar vs Ramesh Gowda).",
        behavior=behavior,
        parameters=types.Schema(
            type=types.Type.OBJECT,
            properties={
                "transcript": types.Schema(type=types.Type.STRING),
                "selected_customer": types.Schema(type=types.Type.STRING),
            },
            required=["transcript", "selected_customer"],
        ),
    )
    confirm_fn = types.FunctionDeclaration(
        name="confirm_pending_action",
        description="Confirm or cancel a pending ambiguous transaction after asking the shopkeeper yes/no.",
        behavior=behavior,
        parameters=types.Schema(
            type=types.Type.OBJECT,
            properties={
                "transcript": types.Schema(type=types.Type.STRING),
                "confirmed": types.Schema(type=types.Type.BOOLEAN),
            },
            required=["transcript", "confirmed"],
        ),
    )
    query_fn = types.FunctionDeclaration(
        name="query_khata_ledger",
        description=(
            "Query exact SQL/MongoDB balances, daily credit/payment totals WITH per-customer names and amounts "
            "(customer_breakdown + transactions), top debtor, outstanding breakdown, or all customers."
        ),
        behavior=behavior,
        parameters=types.Schema(
            type=types.Type.OBJECT,
            properties={
                "transcript": types.Schema(type=types.Type.STRING),
                "kind": types.Schema(
                    type=types.Type.STRING,
                    enum=[
                        "customer_balance",
                        "customer_history",
                        "credit_sum_for_date",
                        "payment_sum_for_date",
                        "top_debtor",
                        "total_outstanding",
                        "all_customers",
                    ],
                ),
                "customer_name": types.Schema(type=types.Type.STRING),
                "date_phrase": types.Schema(type=types.Type.STRING),
            },
            required=["transcript", "kind"],
        ),
    )
    nav_fn = types.FunctionDeclaration(
        name="open_ui_page",
        description="Navigate the app screen to Customers details, Khata dashboard, Settings, or a specific customer's khata.",
        behavior=behavior,
        parameters=types.Schema(
            type=types.Type.OBJECT,
            properties={
                "transcript": types.Schema(type=types.Type.STRING),
                "page": types.Schema(
                    type=types.Type.STRING,
                    enum=["home", "customers", "settings"],
                ),
                "customer_name": types.Schema(type=types.Type.STRING),
            },
            required=["transcript", "page"],
        ),
    )
    create_cust_fn = types.FunctionDeclaration(
        name="create_khata_customer",
        description="Add a new customer to the shop's Khata customer roster (optionally with phone number and reminder opt-in).",
        behavior=behavior,
        parameters=types.Schema(
            type=types.Type.OBJECT,
            properties={
                "transcript": types.Schema(type=types.Type.STRING),
                "customer_name": types.Schema(type=types.Type.STRING),
                "phone": types.Schema(type=types.Type.STRING),
                "reminder_opt_in": types.Schema(type=types.Type.BOOLEAN),
            },
            required=["transcript", "customer_name"],
        ),
    )
    update_cust_fn = types.FunctionDeclaration(
        name="update_khata_customer",
        description="Edit/update an existing customer's profile details (name, phone, reminder opt-in, preferred language). Note: NEVER use this or any tool to delete a customer.",
        behavior=behavior,
        parameters=types.Schema(
            type=types.Type.OBJECT,
            properties={
                "transcript": types.Schema(type=types.Type.STRING),
                "customer_name": types.Schema(type=types.Type.STRING),
                "new_name": types.Schema(type=types.Type.STRING),
                "phone": types.Schema(type=types.Type.STRING),
                "reminder_opt_in": types.Schema(type=types.Type.BOOLEAN),
                "preferred_language": types.Schema(
                    type=types.Type.STRING,
                    enum=["hi-IN", "kn-IN", "ta-IN", "te-IN", "en-IN"],
                ),
            },
            required=["transcript", "customer_name"],
        ),
    )
    return [types.Tool(function_declarations=[record_fn, correct_fn, resolve_fn, confirm_fn, query_fn, nav_fn, create_cust_fn, update_cust_fn])]


def _execute_live_tool(
    ledger: LedgerEngine,
    session_id: str,
    language: str,
    model_used: str,
    tool_name: str,
    args: dict[str, Any],
    ephemeral_transcripts: bool = False,
    interactive_confirm: bool = False,
):
    t0 = time.perf_counter()
    transcript = str(args.get("transcript", ""))
    if tool_name == "record_khata_operations":
        ops = [ExtractedOperation(**op) for op in args.get("operations", [])]
        plan = VoiceTurnPlan(transcript=transcript, intent="record_transactions", operations=ops)
    elif tool_name == "apply_spoken_correction":
        plan = VoiceTurnPlan(
            transcript=transcript,
            intent="correction",
            corrected_amount=args.get("corrected_amount"),
            corrected_tx_type=args.get("corrected_tx_type"),
        )
    elif tool_name == "resolve_pending_customer":
        plan = VoiceTurnPlan(
            transcript=transcript,
            intent="disambiguation_reply",
            selected_customer=args.get("selected_customer"),
        )
    elif tool_name == "confirm_pending_action":
        plan = VoiceTurnPlan(
            transcript=transcript,
            intent="confirmation_reply",
            confirmed=bool(args.get("confirmed")),
        )
    elif tool_name == "open_ui_page":
        plan = VoiceTurnPlan(
            transcript=transcript,
            intent="navigate_page",
            target_page=args.get("page", "customers"),
            selected_customer=args.get("customer_name"),
        )
    elif tool_name == "create_khata_customer":
        plan = VoiceTurnPlan(
            transcript=transcript,
            intent="create_customer",
            new_customer_name=args.get("customer_name"),
            new_customer_phone=args.get("phone"),
            new_customer_opt_in=bool(args.get("reminder_opt_in", False)),
        )
    elif tool_name == "update_khata_customer":
        plan = VoiceTurnPlan(
            transcript=transcript,
            intent="update_customer",
            edit_target_customer=args.get("customer_name"),
            edit_new_name=args.get("new_name"),
            edit_new_phone=args.get("phone"),
            edit_opt_in=args.get("reminder_opt_in"),
            edit_preferred_language=args.get("preferred_language"),
        )
    else:
        plan = VoiceTurnPlan(
            transcript=transcript,
            intent="query_ledger",
            query=ExtractedQuery(
                kind=args.get("kind", "total_outstanding"),
                customer_name=args.get("customer_name"),
                date_phrase=args.get("date_phrase", "today"),
            ),
        )

    res = ledger.process_turn(
        session_id=session_id,
        plan=plan,
        language=language,  # type: ignore[arg-type]
        model_used=model_used,
        latency=LatencyBreakdown(),
        ephemeral_transcripts=ephemeral_transcripts,
        channel="gemini_live",
        interactive_confirm=interactive_confirm,
    )
    ledger_ms = int((time.perf_counter() - t0) * 1000)
    res.latency = LatencyBreakdown(ledger_ms=ledger_ms, total_ms=ledger_ms)
    ledger.update_voice_log_latency(res.voice_log_id, ledger_ms)
    return res


_LIVE_CLIENTS: dict[tuple[str, str], Any] = {}


def _get_live_client(cfg: Settings):
    from google import genai
    from google.genai import types

    key = (cfg.gemini_api_key, cfg.gemini_api_version)
    client = _LIVE_CLIENTS.get(key)
    if client is None:
        client = genai.Client(
            api_key=cfg.gemini_api_key,
            http_options=types.HttpOptions(api_version=cfg.gemini_api_version),
        )
        _LIVE_CLIENTS[key] = client
    return client


async def run_gemini_live_bridge(
    websocket: WebSocket,
    ledger: LedgerEngine,
    session_id: str,
    language: str = "hi-IN",
    settings: Settings | None = None,
    resume_handle: str | None = None,
    extended_thinking: bool = False,
    ephemeral_transcripts: bool = False,
    interactive_confirm: bool = False,
    greet: bool = False,
    voice_name: str | None = None,
) -> None:
    """Bridge browser WebSocket PCM audio (16kHz in / 24kHz out) with Gemini 3.8 Live API."""
    cfg = settings or get_settings()
    await websocket.accept()

    if not cfg.has_gemini:
        await websocket.send_json(
            {
                "type": "error",
                "recoverable": False,
                "message": "GEMINI_API_KEY is not configured. Use Push-to-Talk REST mode or add your key in .env.",
            }
        )
        await websocket.close()
        return

    from google.genai import types

    # Critical fix: Gemini 3.8 Live BidiGenerateContent requires v1beta (SDK 3.1+ defaults to v1)
    client = _get_live_client(cfg)

    known_customers = await asyncio.to_thread(ledger.list_customer_names)
    pending_action = await asyncio.to_thread(ledger.get_pending_action, session_id)
    disclosure = get_ai_disclosure(language)
    live_model = cfg.gemini_live_extended_model if extended_thinking else cfg.gemini_live_model

    system_text = build_kirana_system_prompt(
        language=language,
        known_customers=known_customers,
        pending_action=pending_action,
        disclosure_text=disclosure,
        is_live=True,
        interactive_confirm=interactive_confirm,
    )

    selected_voice = voice_name or cfg.gemini_voice_name
    config_kwargs: dict[str, Any] = {
        "response_modalities": [types.Modality.AUDIO],
        "input_audio_transcription": types.AudioTranscriptionConfig(),
        "output_audio_transcription": types.AudioTranscriptionConfig(),
        "speech_config": types.SpeechConfig(
            voice_config=types.VoiceConfig(
                prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name=selected_voice)
            )
        ),
        "realtime_input_config": types.RealtimeInputConfig(
            automatic_activity_detection=types.AutomaticActivityDetection(
                start_of_speech_sensitivity=types.StartSensitivity.START_SENSITIVITY_HIGH,
                end_of_speech_sensitivity=types.EndSensitivity.END_SENSITIVITY_HIGH,
                prefix_padding_ms=220,
                silence_duration_ms=650,
            )
        ),
        "context_window_compression": types.ContextWindowCompressionConfig(
            sliding_window=types.SlidingWindow()
        ),
        "session_resumption": types.SessionResumptionConfig(handle=resume_handle or None),
        "tools": _build_live_tools(extended_thinking=extended_thinking),
        "system_instruction": types.Content(parts=[types.Part(text=system_text)]),
    }
    if extended_thinking:
        config_kwargs["thinking_config"] = types.ThinkingConfig(thinking_level="low")

    live_config = types.LiveConnectConfig(**config_kwargs)

    try:
        async with client.aio.live.connect(model=live_model, config=live_config) as session:
            await websocket.send_json(
                {
                    "type": "ready",
                    "model": live_model,
                    "api_version": cfg.gemini_api_version,
                    "resumed": bool(resume_handle),
                    "interactive_confirm": interactive_confirm,
                    "voice": selected_voice,
                }
            )
            if greet and not resume_handle:
                greet_phrase = (
                    "ನಿಮ್ಮ ಸೇವೆಗೆ ಸಿದ್ಧವಾಗಿದೆ, ಸರ್."
                    if language == "kn-IN"
                    else "At your service, sir."
                )
                await session.send_realtime_input(
                    text=(
                        f"Begin the session now: speak this mandatory first-turn disclosure once clearly: "
                        f"\"{disclosure}\" Then add a crisp greeting: \"{greet_phrase}\""
                    )
                )
            turn_t0: float | None = None
            first_response_ms: int | None = None
            pcm_frames_in = 0
            logged_first_pcm = False
            logged_first_voice = False

            def _has_voice_energy(pcm_bytes: bytes, threshold: int = 220) -> bool:
                # Sample every 4th 16-bit little-endian PCM sample to spot speech vs ambient silence cheaply
                step = 8
                for off in range(0, len(pcm_bytes) - 1, step):
                    val = struct.unpack_from("<h", pcm_bytes, off)[0]
                    if abs(val) > threshold:
                        return True
                return False

            async def _browser_to_gemini() -> None:
                nonlocal turn_t0, first_response_ms, pcm_frames_in, logged_first_pcm, logged_first_voice
                while True:
                    ws_msg = await websocket.receive()
                    if ws_msg.get("type") == "websocket.disconnect":
                        break
                    if ws_msg.get("bytes"):
                        raw_pcm = ws_msg["bytes"]
                        pcm_frames_in += 1
                        if not logged_first_pcm:
                            logged_first_pcm = True
                            logger.info("Live WS %s receiving binary PCM frames (%d bytes)", session_id, len(raw_pcm))
                        has_voice = _has_voice_energy(raw_pcm)
                        if has_voice and not logged_first_voice:
                            logged_first_voice = True
                            logger.info("Live WS %s detected non-zero voice energy in PCM", session_id)
                        if turn_t0 is None and has_voice:
                            turn_t0 = time.perf_counter()
                            first_response_ms = None
                        await session.send_realtime_input(
                            audio=types.Blob(data=raw_pcm, mime_type="audio/pcm;rate=16000")
                        )
                        continue
                    raw_text = ws_msg.get("text")
                    if not raw_text:
                        continue
                    payload = json.loads(raw_text)
                    msg_type = payload.get("type")
                    if msg_type == "audio_pcm":
                        pcm = base64.b64decode(payload["data"])
                        if turn_t0 is None and _has_voice_energy(pcm):
                            turn_t0 = time.perf_counter()
                            first_response_ms = None
                        await session.send_realtime_input(
                            audio=types.Blob(data=pcm, mime_type="audio/pcm;rate=16000")
                        )
                    elif msg_type == "text":
                        turn_t0 = time.perf_counter()
                        first_response_ms = None
                        await session.send_realtime_input(text=payload.get("text", ""))
                    elif msg_type == "end_turn":
                        if turn_t0 is None:
                            turn_t0 = time.perf_counter()
                            first_response_ms = None
                        logger.info("Live WS %s client sent end_turn after %d PCM frames", session_id, pcm_frames_in)
                        await session.send_realtime_input(audio_stream_end=True)
                    elif msg_type == "ping":
                        await websocket.send_json({"type": "pong"})

            async def _handle_live_response(response: Any) -> None:
                nonlocal turn_t0, first_response_ms
                if response.session_resumption_update:
                    upd = response.session_resumption_update
                    if upd.resumable and upd.new_handle:
                        await websocket.send_json({"type": "session_handle", "handle": upd.new_handle})

                if getattr(response, "go_away", None):
                    await websocket.send_json({"type": "go_away", "recoverable": True})

                if response.tool_call:
                    if turn_t0 is not None and first_response_ms is None:
                        first_response_ms = int((time.perf_counter() - turn_t0) * 1000)
                    fn_responses = []
                    for fc in response.tool_call.function_calls:
                        args: dict[str, Any] = dict(fc.args or {})
                        turn_res = await asyncio.to_thread(
                            _execute_live_tool,
                            ledger,
                            session_id,
                            language,
                            live_model,
                            fc.name,
                            args,
                            ephemeral_transcripts,
                            interactive_confirm,
                        )
                        elapsed_ms = (
                            int((time.perf_counter() - turn_t0) * 1000)
                            if turn_t0
                            else turn_res.latency.ledger_ms
                        )
                        turn_res.latency = LatencyBreakdown(
                            ai_ms=max(0, elapsed_ms - turn_res.latency.ledger_ms),
                            ledger_ms=turn_res.latency.ledger_ms,
                            total_ms=elapsed_ms,
                        )
                        await asyncio.to_thread(ledger.update_voice_log_latency, turn_res.voice_log_id, elapsed_ms)
                        await websocket.send_json({"type": "ledger_event", "payload": turn_res.model_dump()})
                        fn_responses.append(
                            types.FunctionResponse(
                                name=fc.name,
                                id=fc.id,
                                response={"result": turn_res.model_dump()},
                            )
                        )
                    await session.send_tool_response(function_responses=fn_responses)

                server_content = response.server_content
                if server_content:
                    if getattr(server_content, "interrupted", False) is True:
                        await websocket.send_json({"type": "interrupted"})
                    if getattr(server_content, "input_transcription", None) and server_content.input_transcription.text:
                        await websocket.send_json(
                            {"type": "input_transcript", "text": server_content.input_transcription.text}
                        )
                    if getattr(server_content, "output_transcription", None) and server_content.output_transcription.text:
                        await websocket.send_json(
                            {"type": "output_transcript", "text": server_content.output_transcription.text}
                        )
                    if server_content.model_turn:
                        for part in server_content.model_turn.parts:
                            if part.inline_data and part.inline_data.data:
                                if turn_t0 is not None and first_response_ms is None:
                                    first_response_ms = int((time.perf_counter() - turn_t0) * 1000)
                                audio_b64 = (
                                    part.inline_data.data
                                    if isinstance(part.inline_data.data, str)
                                    else base64.b64encode(part.inline_data.data).decode("ascii")
                                )
                                await websocket.send_json(
                                    {"type": "audio_pcm", "data": audio_b64, "sample_rate": 24000}
                                )
                            if part.text:
                                await websocket.send_json({"type": "text_delta", "text": part.text})
                    if getattr(server_content, "turn_complete", False):
                        total_ms = int((time.perf_counter() - turn_t0) * 1000) if turn_t0 else 0
                        ttfb_ms = first_response_ms if first_response_ms is not None else total_ms
                        turn_t0 = None
                        first_response_ms = None
                        await websocket.send_json(
                            {"type": "turn_complete", "ttfb_ms": ttfb_ms, "latency_ms": total_ms}
                        )

            async def _gemini_to_browser() -> None:
                # AsyncSession.receive() breaks after each turn_complete; loop to sustain multi-turn conversation
                while True:
                    received_any = False
                    async for response in session.receive():
                        received_any = True
                        await _handle_live_response(response)
                    if not received_any:
                        break

            t_in = asyncio.create_task(_browser_to_gemini())
            t_out = asyncio.create_task(_gemini_to_browser())
            done, pending = await asyncio.wait({t_in, t_out}, return_when=asyncio.FIRST_COMPLETED)
            for task in pending:
                task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)
            for task in done:
                exc = task.exception()
                if exc and not isinstance(exc, WebSocketDisconnect):
                    raise exc
    except WebSocketDisconnect:
        return
    except Exception as exc:
        logger.warning("Live WS %s bridge interrupted (%s): %s", session_id, type(exc).__name__, exc)
        try:
            await websocket.send_json(
                {
                    "type": "error",
                    "recoverable": True,
                    "message": f"Live stream interrupted ({type(exc).__name__}): {exc}",
                }
            )
        except Exception:
            pass
