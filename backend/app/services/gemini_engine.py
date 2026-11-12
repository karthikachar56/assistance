"""Top-end Gemini AI Interactions Engine (3.8 Flash -> 4 Argon / 4 Carbon + 3.8 Flash-Lite TTS)."""

from __future__ import annotations

import base64
import json
import logging
import time
from typing import Any

from app.config import Settings, get_settings
from app.schemas.models import LanguageCode, VoiceTurnPlan
from app.services.number_parser import extract_numbers, normalize_indic_digits
from app.services.training_examples import build_kirana_system_prompt

logger = logging.getLogger(__name__)


class GeminiKhataEngine:
    """Cascaded Gemini 3.8 Flash -> Gemini 3.5 Flash-Lite / 4 Argon Interactions API extractor & 3.8 TTS."""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self._client: Any | None = None

    def _get_client(self):
        if not self.settings.has_gemini:
            return None
        if self._client is None:
            from google import genai
            from google.genai import types

            self._client = genai.Client(
                api_key=self.settings.gemini_api_key,
                http_options=types.HttpOptions(
                    api_version=self.settings.gemini_api_version,
                    timeout=4500,
                    retry_options=types.HttpRetryOptions(attempts=1),
                ),
            )
        return self._client

    def _build_context_prompt(
        self,
        language: LanguageCode,
        known_customers: list[str],
        pending_action: dict | None,
        transcript: str | None = None,
    ) -> str:
        customers_hint = ", ".join(known_customers[:50]) if known_customers else "None yet"
        pending_hint = json.dumps(pending_action) if pending_action else "None"
        base = (
            f"Preferred shopkeeper language: {language}\n"
            f"Known customers: {customers_hint}\n"
            f"Active pending_action state: {pending_hint}\n"
        )
        if transcript is not None:
            clean_transcript = normalize_indic_digits(transcript)
            detected_nums = [n.value for n in extract_numbers(clean_transcript)]
            num_hint = f"Deterministic extracted numbers: {detected_nums}\n" if detected_nums else ""
            return f"{base}{num_hint}Spoken utterance transcript: {clean_transcript}"
        return f"{base}Listen carefully to the attached shop audio (ignoring street/counter noise) and return the exact VoiceTurnPlan JSON."

    def _call_model(
        self,
        model_id: str,
        thinking_level: str | None,
        system_instruction: str,
        input_payload: Any,
    ) -> VoiceTurnPlan:
        client = self._get_client()
        if client is None:
            raise RuntimeError("GEMINI_API_KEY is not configured.")

        kwargs: dict[str, Any] = {
            "model": model_id,
            "system_instruction": system_instruction,
            "input": input_payload,
            "response_format": {
                "type": "text",
                "mime_type": "application/json",
                "schema": VoiceTurnPlan.model_json_schema(),
            },
            "store": False,
        }
        if thinking_level:
            kwargs["generation_config"] = {"thinking_level": thinking_level}

        interaction = client.interactions.create(**kwargs)
        return VoiceTurnPlan.model_validate_json(interaction.output_text)

    def _call_with_fallback(
        self,
        candidates: list[tuple[str, str | None]],
        system_instruction: str,
        input_payload: Any,
        *,
        deadline_sec: float = 7.2,
    ) -> tuple[VoiceTurnPlan, str]:
        """Try prioritized Gemini models in order within a serverless-safe time budget."""
        last_exc: Exception | None = None
        seen: set[tuple[str, str | None]] = set()
        t_start = time.monotonic()
        for idx, (model_id, thinking_level) in enumerate(candidates):
            if (model_id, thinking_level) in seen:
                continue
            if idx > 0 and (time.monotonic() - t_start) > deadline_sec:
                break
            seen.add((model_id, thinking_level))
            try:
                return self._call_model(model_id, thinking_level, system_instruction, input_payload), model_id
            except Exception as exc:
                last_exc = exc
                logger.warning("Gemini model %s failed (%s): %s", model_id, type(exc).__name__, exc)
        assert last_exc is not None
        raise last_exc

    def parse_turn(
        self,
        *,
        language: LanguageCode,
        known_customers: list[str],
        pending_action: dict | None,
        ai_mode: str = "cascaded",
        transcript: str | None = None,
        audio_bytes: bytes | None = None,
        audio_mime_type: str = "audio/wav",
    ) -> tuple[VoiceTurnPlan, str]:
        """Parse text or raw audio using resilient Interactions API cascade."""
        t_total = time.monotonic()
        system_prompt = build_kirana_system_prompt(
            language=language,
            known_customers=known_customers,
            pending_action=pending_action,
            is_live=False,
        )
        prompt_text = self._build_context_prompt(
            language=language,
            known_customers=known_customers,
            pending_action=pending_action,
            transcript=transcript,
        )
        if audio_bytes is not None:
            input_payload: Any = [
                {
                    "type": "audio",
                    "data": base64.b64encode(audio_bytes).decode("ascii"),
                    "mime_type": audio_mime_type,
                },
                {"type": "text", "text": prompt_text},
            ]
        else:
            input_payload = prompt_text

        fast_candidates: list[tuple[str, str | None]] = [
            (self.settings.gemini_fast_model, "low"),
            (self.settings.gemini_lite_model, None),
        ]

        if ai_mode == "carbon":
            return self._call_with_fallback(
                [(self.settings.gemini_carbon_model, "medium"), *fast_candidates],
                system_prompt,
                input_payload,
            )

        if ai_mode == "argon":
            return self._call_with_fallback(
                [(self.settings.gemini_pro_model, "medium"), *fast_candidates],
                system_prompt,
                input_payload,
            )

        # Default fast tier with resilient 503/429 fallback across Flash -> Flash-Lite -> 3.5 Flash
        plan, model_used = self._call_with_fallback(fast_candidates, system_prompt, input_payload)

        # Escalate to flagship Gemini 4 Argon when confidence is low or ambiguity arises (fail-soft on quota)
        should_escalate = ai_mode == "cascaded" and (
            plan.confidence < self.settings.escalation_confidence_threshold
            or plan.intent == "unknown"
            or any(op.direction_ambiguous or bool(op.ambiguous_amounts) for op in plan.operations)
        )
        if should_escalate and (time.monotonic() - t_total) < 4.5:
            try:
                pro_plan = self._call_model(self.settings.gemini_pro_model, "medium", system_prompt, input_payload)
                if pro_plan.confidence >= plan.confidence:
                    return pro_plan, f"{model_used} -> {self.settings.gemini_pro_model}"
            except Exception as exc:
                logger.info("Escalation skipped (%s): %s", type(exc).__name__, exc)

        return plan, model_used

    def synthesize_tts(self, text: str, language: LanguageCode = "hi-IN") -> tuple[str, str] | None:
        """Synthesize natural Indic/code-mixed WAV audio using Gemini 3.8 TTS via Interactions API (v1beta)."""
        client = self._get_client()
        if client is None:
            return None

        for tts_model in (self.settings.gemini_tts_model, "gemini-3.8-flash-tts"):
            try:
                interaction = client.interactions.create(
                    model=tts_model,
                    input=[
                        {
                            "type": "user_input",
                            "content": [
                                {
                                    "type": "text",
                                    "text": text,
                                    "annotations": [
                                        {
                                            "type": "speech_metadata",
                                            "style": f"clear, polite, concise kirana assistant reply in {language}",
                                        }
                                    ],
                                }
                            ],
                        }
                    ],
                    response_format={"type": "audio", "mime_type": "audio/wav"},
                    generation_config={"speech_config": [{"voice": self.settings.gemini_voice_name}]},
                    store=False,
                )
                output_audio = getattr(interaction, "output_audio", None)
                if output_audio and getattr(output_audio, "data", None):
                    return output_audio.data, getattr(output_audio, "mime_type", None) or "audio/wav"
            except Exception as exc:
                logger.warning("Gemini TTS model %s failed (%s): %s", tts_model, type(exc).__name__, exc)
        return None
