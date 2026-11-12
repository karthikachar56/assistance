import React from "react";
import { Lock, Trash2 } from "lucide-react";

const LANGUAGES = [
  { code: "hi-IN", label: "Hindi / Hinglish (hi-IN)" },
  { code: "kn-IN", label: "Kannada (ಕನ್ನಡ · kn-IN)" },
  { code: "ta-IN", label: "Tamil / Tanglish (ta-IN)" },
  { code: "te-IN", label: "Telugu / Tenglish (te-IN)" },
  { code: "en-IN", label: "Indian English (en-IN)" },
];

export function SettingsScreen({
  statusInfo,
  language,
  aiMode,
  speechProvider,
  ephemeral,
  useDirectAudio,
  useLiveStream,
  liveWsSupported = true,
  liveEphemeralSupported = false,
  canLiveStream = liveWsSupported || liveEphemeralSupported,
  interactiveConfirm,
  onUpdateSetting,
  onToggleDirectAudio,
  onToggleLiveStream,
  onToggleInteractiveConfirm,
  onWipeTranscripts,
}) {
  return (
    <div style={{ display: "grid", gap: 14 }}>
      <div className="card">
        <h3 style={{ marginTop: 0 }}>Language &amp; Gemini AI Engine</h3>
        <div style={{ display: "grid", gap: 12 }}>
          <label style={{ display: "grid", gap: 4, fontSize: "0.85rem", fontWeight: 600 }}>
            Preferred Shopkeeper Language
            <select
              className="text-input"
              value={language}
              onChange={(e) => onUpdateSetting({ preferred_language: e.target.value })}
            >
              {LANGUAGES.map((l) => (
                <option key={l.code} value={l.code}>
                  {l.label}
                </option>
              ))}
            </select>
          </label>

          <label style={{ display: "grid", gap: 4, fontSize: "0.85rem", fontWeight: 600 }}>
            Gemini Optimization Tier
            <select
              className="text-input"
              value={aiMode}
              onChange={(e) => onUpdateSetting({ ai_mode: e.target.value })}
            >
              <option value="cascaded">
                Cascaded (Recommended: Fast-Path + Gemini 3.8 Flash + Gemini 4 Argon escalation)
              </option>
              <option value="flash">Gemini 3.8 Flash Only (Lowest cloud latency)</option>
              <option value="argon">Gemini 4 Argon (Fast frontier reasoning)</option>
              <option value="carbon">Gemini 4 Carbon (Top-end maximum reasoning)</option>
              <option value="offline">Local Deterministic Fast-Path Only</option>
            </select>
          </label>

          <label style={{ display: "grid", gap: 4, fontSize: "0.85rem", fontWeight: 600 }}>
            Speech Recognition &amp; TTS Provider
            <select
              className="text-input"
              value={speechProvider}
              onChange={(e) => onUpdateSetting({ speech_provider: e.target.value })}
            >
              <option value="auto">Auto (Instant &lt;2s Voice + Gemini Live 24kHz Audio)</option>
              <option value="gemini">Gemini 3.8 Native Cloud TTS (Rich Indic WAV Audio)</option>
              <option value="gnani">Gnani Prisma v3.0 STT &amp; Timbre v3.0 TTS</option>
              <option value="browser">Browser Web Speech (Zero-cost on-device)</option>
            </select>
          </label>

          <label style={{ display: "flex", alignItems: "center", gap: 8, fontSize: "0.86rem" }}>
            <input type="checkbox" checked={useDirectAudio} onChange={(e) => onToggleDirectAudio(e.target.checked)} />
            Upload 16kHz noise-filtered WAV directly to Gemini / Gnani (best under shop noise)
          </label>

          <label style={{ display: "flex", alignItems: "center", gap: 8, fontSize: "0.86rem" }}>
            <input
              type="checkbox"
              checked={useLiveStream && canLiveStream}
              disabled={!canLiveStream}
              onChange={(e) => onToggleLiveStream(e.target.checked)}
            />
            Enable Real-Time Assistant Gemini 3.8 Live Voice Mode
            {!canLiveStream
              ? " (requires Gemini API Key; using Push-to-Talk)"
              : !liveWsSupported && liveEphemeralSupported
              ? " (Direct Cloud Live via Ephemeral Token)"
              : " (/ws/live)"}
          </label>

          <label style={{ display: "flex", alignItems: "center", gap: 8, fontSize: "0.86rem" }}>
            <input
              type="checkbox"
              checked={interactiveConfirm}
              onChange={(e) => onToggleInteractiveConfirm(e.target.checked)}
            />
            Interactive confirmation before saving (&ldquo;Sure, sir. Shall I add this entry?&rdquo;)
          </label>
        </div>
      </div>

      <div className="card">
        <h3 style={{ marginTop: 0, display: "flex", alignItems: "center", gap: 6 }}>
          <Lock size={17} /> Responsible AI &amp; DPDP Act Controls
        </h3>
        <div style={{ fontSize: "0.84rem", color: "var(--muted)", marginBottom: 10 }}>
          Database:{" "}
          <strong>
            {statusInfo?.database_backend === "mongodb"
              ? `MongoDB (${statusInfo?.database_name || "voice_khata"})`
              : `SQLite (${statusInfo?.database_name || "voice_khata.db"})`}
          </strong>{" "}
          · Storage Encryption: <strong>{statusInfo?.encryption_at_rest || "AES-256-GCM"}</strong> · Gemini API:{" "}
          <strong>{statusInfo?.gemini_configured ? "Connected" : "Offline/Fallback"}</strong> · Gnani API:{" "}
          <strong>{statusInfo?.gnani_configured ? "Connected" : "Adapter Ready"}</strong>
        </div>
        {statusInfo?.storage_fallback_reason && (
          <div
            style={{
              fontSize: "0.8rem",
              color: "#92400e",
              background: "#fef3c7",
              border: "1px solid #f59e0b",
              borderRadius: 8,
              padding: "8px 10px",
              marginBottom: 12,
            }}
          >
            {statusInfo.storage_fallback_reason}
          </div>
        )}

        <label style={{ display: "flex", alignItems: "center", gap: 8, fontSize: "0.86rem", marginBottom: 14 }}>
          <input
            type="checkbox"
            checked={ephemeral}
            onChange={(e) => onUpdateSetting({ ephemeral_transcripts: e.target.checked })}
          />
          Ephemeral transcript mode (do not persist raw spoken transcripts in database)
        </label>

        <button className="btn btn-danger" onClick={onWipeTranscripts}>
          <Trash2 size={15} /> Wipe Demo Transcripts Now (DPDP)
        </button>
      </div>
    </div>
  );
}

