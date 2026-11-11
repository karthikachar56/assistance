"""Optional Gnani AI Platform adapter (Prisma STT & Timbre TTS via gnani-vachana)."""

from __future__ import annotations

import base64
import os
import tempfile

from app.config import Settings, get_settings
from app.schemas.models import LanguageCode


class GnaniSpeechAdapter:
    """Wraps Gnani Prisma STT and Timbre TTS when GNANI_API_KEY and gnani-vachana are present."""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()

    @property
    def is_available(self) -> bool:
        if not self.settings.has_gnani:
            return False
        try:
            import gnani  # noqa: F401
            return True
        except ImportError:
            return False

    def transcribe_audio(self, audio_bytes: bytes, language: LanguageCode = "hi-IN", suffix: str = ".wav") -> str:
        """Transcribe short shopkeeper audio using Gnani Prisma STT."""
        from gnani.stt import GnaniSTTClient

        client = GnaniSTTClient(api_key=self.settings.gnani_api_key)
        fd, tmp_path = tempfile.mkstemp(suffix=suffix)
        try:
            with os.fdopen(fd, "wb") as tmp:
                tmp.write(audio_bytes)
            result = client.transcribe(
                audio_file=tmp_path,
                model=self.settings.gnani_stt_model,
                language_code=language,
            )
            return str(result.get("transcript", "")).strip()
        finally:
            try:
                os.remove(tmp_path)
            except OSError:
                pass

    def synthesize_speech(self, text: str, language: LanguageCode = "hi-IN") -> tuple[str, str]:
        """Synthesize natural Indic voice response using Gnani Timbre TTS."""
        from gnani.tts import AudioConfig, GnaniTTSClient

        client = GnaniTTSClient(api_key=self.settings.gnani_api_key)
        audio = client.synthesize(
            text=text,
            voice=self.settings.gnani_tts_voice,
            model=self.settings.gnani_tts_model,
            language=language,
            speed=1.0,
            audio_config=AudioConfig(sample_rate=24000, encoding="linear_pcm", container="wav"),
        )
        return base64.b64encode(audio).decode("ascii"), "audio/wav"
