import React from "react";
import { Check, Mic, MicOff, Radio, ShieldCheck, Sparkles, Volume2, X } from "lucide-react";
import { playReplyAudio } from "../audio/recorder";
import { previewSpokenAmount } from "../utils/numberNormalizer";

export function JarvisHud({
  liveConnected,
  liveConnecting,
  aiSpeaking,
  isRecording,
  isProcessing,
  useLiveStream,
  interactiveConfirm,
  geminiConfigured,
  liveWsSupported = true,
  micLevel,
  micInfo,
  liveUserText,
  liveAiText,
  conversationFeed,
  lastTurn,
  liveMetrics,
  language,
  onToggleMic,
  onToggleLiveStream,
  onToggleInteractiveConfirm,
  onQuickReply,
}) {
  const isListening = liveConnected || isRecording;
  const awaitingCustName =
    lastTurn?.status === "needs_confirmation" && lastTurn?.pending_action?.kind === "create_customer";
  const awaitingConfirm =
    lastTurn?.status === "needs_confirmation" && !awaitingCustName && Boolean(lastTurn?.pending_action);
  const awaitingDisambig = lastTurn?.status === "needs_disambiguation";

  let statusLabel = "STANDBY · TAP TO SPEAK";
  let modeClass = "idle";
  if (liveConnecting || isProcessing) {
    statusLabel = "PROCESSING · NEURAL LINK";
    modeClass = "thinking";
  } else if (aiSpeaking) {
    statusLabel = "ASSISTANT SPEAKING";
    modeClass = "speaking";
  } else if (awaitingCustName) {
    statusLabel = "AWAITING NEW CUSTOMER NAME";
    modeClass = "confirm";
  } else if (awaitingConfirm) {
    statusLabel = "AWAITING CONFIRMATION ('YES' / 'NO')";
    modeClass = "confirm";
  } else if (isListening) {
    statusLabel = liveConnected ? "ASSISTANT LIVE · LISTENING" : "PUSH-TO-TALK · LISTENING";
    modeClass = "listening";
  }

  const orbScale = isListening ? 1 + Math.min(0.28, micLevel * 0.35) : aiSpeaking ? 1.12 : 1;
  const detectedNum = previewSpokenAmount(liveUserText);

  return (
    <section className={`jarvis-hud ${modeClass}`} aria-label="Assistant Live Voice HUD">
      <div className="jarvis-top">
        <div className="jarvis-orb-wrap">
          <button
            type="button"
            className={`jarvis-orb ${modeClass}`}
            style={{ transform: `scale(${orbScale})` }}
            onClick={onToggleMic}
            disabled={isProcessing}
            title={isListening ? "Tap to stop listening" : "Tap to wake Assistant voice"}
          >
            <span className="jarvis-ring jarvis-ring-outer" />
            <span className="jarvis-ring jarvis-ring-inner" />
            {isListening ? (
              useLiveStream ? <Radio size={24} /> : <MicOff size={24} />
            ) : (
              <Mic size={24} />
            )}
          </button>
          <div>
            <div className="jarvis-badge-row">
              <span className={`jarvis-status-dot ${modeClass}`} />
              <span className="jarvis-status-text">{statusLabel}</span>
              {liveMetrics?.ttfb_ms > 0 && (
                <span className="jarvis-latency-pill">TTFB {liveMetrics.ttfb_ms}ms</span>
              )}
            </div>
            <div className="jarvis-subtitle">
              {useLiveStream && liveWsSupported
                ? "Real-time voice + live transcript"
                : !liveWsSupported
                ? "Push-to-Talk Voice · Cloud Audio & Instant Browser STT"
                : "Instant Browser STT + Resilient Cloud Audio Fallback"}
            </div>
          </div>
        </div>

        <div className="jarvis-controls">
          <button
            type="button"
            className={`jarvis-pill-btn ${useLiveStream ? "active" : ""}`}
            onClick={() => onToggleLiveStream(!useLiveStream)}
            disabled={!geminiConfigured || !liveWsSupported}
            title={
              !liveWsSupported
                ? "Real-time /ws/live requires a WebSocket server (Push-to-Talk & Cloud Audio REST are active)"
                : geminiConfigured
                ? "Switch between Gemini Live Stream and Push-to-Talk"
                : "Requires Gemini API Key"
            }
          >
            <Radio size={13} /> {useLiveStream ? "Live AI: ON" : "Push-to-Talk"}
          </button>
          <button
            type="button"
            className={`jarvis-pill-btn ${interactiveConfirm ? "active" : ""}`}
            onClick={() => onToggleInteractiveConfirm(!interactiveConfirm)}
            title="Ask 'Sure, sir. Shall I confirm?' before saving credits or payments"
          >
            <ShieldCheck size={13} /> Confirm Before Save: {interactiveConfirm ? "ON" : "OFF"}
          </button>
        </div>
      </div>

      {/* Real-time scrolling caption & assistant dialogue */}
      <div className="jarvis-transcript-grid">
        <div className="jarvis-caption-box">
          <div className="jarvis-caption-label">
            <span>YOU · LIVE SPEECH TO TEXT</span>
            <span style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
              {isListening && (
                <span className="jarvis-meter-track" title={`Mic level: ${Math.round(micLevel * 100)}%`}>
                  <span
                    className="jarvis-meter-fill"
                    style={{ width: `${Math.max(6, Math.round(micLevel * 100))}%` }}
                  />
                </span>
              )}
              {detectedNum !== null && <span className="jarvis-num-chip">₹{detectedNum}</span>}
            </span>
          </div>
          <div className="jarvis-caption-text">
            {liveUserText ? (
              liveUserText
            ) : isListening && aiSpeaking ? (
              <span style={{ color: "#6ee7b7" }}>
                Assistant is speaking — speak right after it finishes...
              </span>
            ) : isListening && micInfo?.signalOk === false ? (
              <span className="jarvis-mic-warn">
                Mic connected ({micInfo.deviceLabel || "Default"}), but receiving 0% audio — check Windows mic mute/device.
              </span>
            ) : isListening && micInfo?.speechActive ? (
              <span style={{ color: "#38bdf8" }}>
                Hearing your voice cleanly ({micInfo.deviceLabel || "Mic"}) — transcribing...
              </span>
            ) : (
              <span style={{ opacity: 0.65 }}>
                {isListening
                  ? `Listening (${micInfo?.deviceLabel || "Mic ready"}) — say "Add 340 credit to Suresh" or "Ramesh ka 340 udhaar"...`
                  : "Tap the glowing orb or bottom mic and speak — words appear here live."}
              </span>
            )}
          </div>
        </div>

        <div className="jarvis-caption-box jarvis-ai-box">
          <div className="jarvis-caption-label">
            <span>
              <Sparkles size={12} style={{ verticalAlign: "-1px" }} /> ASSISTANT · VOICE KHATA
            </span>
            {lastTurn?.spoken_reply && (
              <button
                type="button"
                className="jarvis-replay-btn"
                onClick={() =>
                  playReplyAudio({
                    audioBase64: lastTurn.audio_base64,
                    audioMimeType: lastTurn.audio_mime_type,
                    text: lastTurn.spoken_reply,
                    language,
                  })
                }
                title="Replay reply"
              >
                <Volume2 size={13} />
              </button>
            )}
          </div>
          <div className="jarvis-caption-text">
            {liveAiText || lastTurn?.spoken_reply || (
              <span style={{ opacity: 0.6 }}>
                Tap the orb to start an interactive Assistant khata session.
              </span>
            )}
          </div>
        </div>
      </div>

      {/* 1-tap Interactive Confirmation / Customer Name / Disambiguation bar */}
      {awaitingCustName && (
        <div className="jarvis-action-bar">
          <span className="jarvis-action-prompt">Speak or type the new customer&rsquo;s name below:</span>
          <button
            type="button"
            className="jarvis-confirm-no"
            disabled={isProcessing}
            onClick={() => onQuickReply("No, cancel")}
          >
            <X size={14} /> Cancel
          </button>
        </div>
      )}

      {awaitingConfirm && (
        <div className="jarvis-action-bar">
          <span className="jarvis-action-prompt">Say &ldquo;Yes, sir&rdquo;, &ldquo;No, 430&rdquo;, or tap:</span>
          <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
            <button
              type="button"
              className="jarvis-confirm-yes"
              disabled={isProcessing}
              onClick={() => onQuickReply("Yes, confirm")}
            >
              <Check size={14} /> Yes, Confirm &amp; Save
            </button>
            <button
              type="button"
              className="jarvis-confirm-no"
              disabled={isProcessing}
              onClick={() => onQuickReply("No, cancel")}
            >
              <X size={14} /> Cancel
            </button>
          </div>
        </div>
      )}

      {awaitingDisambig && lastTurn?.candidate_customers?.length > 0 && (
        <div className="jarvis-action-bar">
          <span className="jarvis-action-prompt">Specify customer:</span>
          <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
            {lastTurn.candidate_customers.map((name) => (
              <button
                key={name}
                type="button"
                className="jarvis-confirm-yes"
                disabled={isProcessing}
                onClick={() => onQuickReply(name)}
              >
                {name}
              </button>
            ))}
          </div>
        </div>
      )}

      {/* Compact live dialog history */}
      {conversationFeed.length > 0 && (
        <div className="jarvis-feed">
          {conversationFeed.slice(-4).map((item) => (
            <div key={item.id} className={`jarvis-feed-line ${item.role}`}>
              <strong>{item.role === "user" ? "You:" : "Assistant:"}</strong> {item.text}
            </div>
          ))}
        </div>
      )}
    </section>
  );
}

