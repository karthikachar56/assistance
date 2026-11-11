/**
 * Noise-filtered 16kHz mono WAV recorder + Web Speech API concurrent STT helper.
 * Applies browser echoCancellation/noiseSuppression + an 85Hz Biquad high-pass filter
 * to attenuate street rumble and shop weighing-machine hum.
 */

function downsampleBuffer(buffer, inputRate, targetRate = 16000) {
  if (targetRate === inputRate) return buffer;
  const ratio = inputRate / targetRate;
  const newLength = Math.round(buffer.length / ratio);
  const result = new Float32Array(newLength);
  let offsetResult = 0;
  let offsetBuffer = 0;
  while (offsetResult < result.length) {
    const nextOffsetBuffer = Math.round((offsetResult + 1) * ratio);
    let accum = 0;
    let count = 0;
    for (let i = offsetBuffer; i < nextOffsetBuffer && i < buffer.length; i += 1) {
      accum += buffer[i];
      count += 1;
    }
    result[offsetResult] = count > 0 ? accum / count : 0;
    offsetResult += 1;
    offsetBuffer = nextOffsetBuffer;
  }
  return result;
}

function encodeWav16k(samples, sampleRate = 16000) {
  const buffer = new ArrayBuffer(44 + samples.length * 2);
  const view = new DataView(buffer);
  const writeString = (offset, str) => {
    for (let i = 0; i < str.length; i += 1) view.setUint8(offset + i, str.charCodeAt(i));
  };
  writeString(0, "RIFF");
  view.setUint32(4, 36 + samples.length * 2, true);
  writeString(8, "WAVE");
  writeString(12, "fmt ");
  view.setUint32(16, 16, true);
  view.setUint16(20, 1, true); // PCM
  view.setUint16(22, 1, true); // Mono
  view.setUint32(24, sampleRate, true);
  view.setUint32(28, sampleRate * 2, true);
  view.setUint16(32, 2, true);
  view.setUint16(34, 16, true);
  writeString(36, "data");
  view.setUint32(40, samples.length * 2, true);

  let offset = 44;
  for (let i = 0; i < samples.length; i += 1) {
    const s = Math.max(-1, Math.min(1, samples[i]));
    view.setInt16(offset, s < 0 ? s * 0x8000 : s * 0x7fff, true);
    offset += 2;
  }
  return new Blob([view], { type: "audio/wav" });
}

/**
 * Reusable continuous Web Speech API caption helper with auto-restart on Chrome idle timeout.
 * Provides sub-50ms word-by-word live captions alongside audio capture.
 */
export function createSpeechCaptioner({ language = "hi-IN", onTranscript, continuousLoop = false } = {}) {
  const SR = typeof window !== "undefined" ? window.SpeechRecognition || window.webkitSpeechRecognition : null;
  if (!SR) return null;

  let active = true;
  let rec = null;
  let accumulatedFinal = "";
  let consecutiveErrors = 0;

  const FATAL_SR_ERRORS = new Set([
    "not-allowed",
    "service-not-allowed",
    "audio-capture",
    "language-not-supported",
    "network",
  ]);

  const startRec = () => {
    if (!active) return;
    try {
      rec = new SR();
      rec.lang = language;
      rec.continuous = true;
      rec.interimResults = true;
      rec.onresult = (event) => {
        consecutiveErrors = 0;
        let finalChunk = "";
        let interimChunk = "";
        for (let i = event.resultIndex; i < event.results.length; i += 1) {
          const piece = event.results[i][0]?.transcript || "";
          if (event.results[i].isFinal) {
            finalChunk += piece + " ";
          } else {
            interimChunk += piece + " ";
          }
        }
        if (finalChunk.trim()) {
          accumulatedFinal = (accumulatedFinal + " " + finalChunk.trim()).trim();
          if (accumulatedFinal.length > 260) {
            accumulatedFinal = accumulatedFinal.slice(-260).trim();
          }
        }
        const liveDisplay = (finalChunk.trim() ? finalChunk.trim() : interimChunk.trim()) || accumulatedFinal;
        onTranscript?.({
          full: accumulatedFinal,
          current: liveDisplay.trim(),
          finalDelta: finalChunk.trim(),
          interim: interimChunk.trim(),
        });
      };
      rec.onerror = (evt) => {
        consecutiveErrors += 1;
        if (FATAL_SR_ERRORS.has(evt?.error) || consecutiveErrors >= 3) {
          active = false;
        }
      };
      rec.onend = () => {
        if (active && continuousLoop) {
          setTimeout(() => {
            if (active) startRec();
          }, 260);
        }
      };
      rec.start();
    } catch {
      active = false;
      rec = null;
    }
  };

  startRec();

  return {
    getTranscript: () => accumulatedFinal.trim(),
    resetCurrent: () => {
      accumulatedFinal = "";
    },
    stop: () => {
      active = false;
      if (rec) {
        try {
          rec.onend = null;
          rec.stop();
        } catch {
          // ignore
        }
        rec = null;
      }
    },
  };
}

export class ShopAudioRecorder {
  constructor() {
    this.stream = null;
    this.ctx = null;
    this.processor = null;
    this.muteGain = null;
    this.chunks = [];
    this.captioner = null;
    this.liveTranscript = "";
  }

  async start({ language = "hi-IN", onLevel, onInterimTranscript } = {}) {
    this.chunks = [];
    this.liveTranscript = "";

    const AudioCtx = window.AudioContext || window.webkitAudioContext;
    this.ctx = new AudioCtx();
    if (this.ctx.state === "suspended") {
      await this.ctx.resume().catch(() => {});
    }

    this.stream = await navigator.mediaDevices.getUserMedia({
      audio: {
        channelCount: 1,
        echoCancellation: true,
        noiseSuppression: true,
        autoGainControl: true,
      },
    });
    if (this.ctx.state === "suspended") {
      await this.ctx.resume().catch(() => {});
    }

    const source = this.ctx.createMediaStreamSource(this.stream);

    // 85Hz high-pass filter cuts street traffic rumble & fan/motor hum
    const highPass = this.ctx.createBiquadFilter();
    highPass.type = "highpass";
    highPass.frequency.value = 85;

    const processor = this.ctx.createScriptProcessor(4096, 1, 1);
    this.processor = processor;
    this.muteGain = this.ctx.createGain();
    this.muteGain.gain.value = 0.0;

    processor.onaudioprocess = (e) => {
      const input = e.inputBuffer.getChannelData(0);
      this.chunks.push(new Float32Array(input));
      if (onLevel) {
        let sum = 0;
        for (let i = 0; i < input.length; i += 1) sum += input[i] * input[i];
        onLevel(Math.min(1, Math.sqrt(sum / input.length) * 10));
      }
    };

    source.connect(highPass);
    highPass.connect(processor);
    processor.connect(this.muteGain);
    this.muteGain.connect(this.ctx.destination);

    // Concurrent browser SpeechRecognition for real-time on-screen word-by-word text
    this.captioner = createSpeechCaptioner({
      language,
      continuousLoop: false,
      onTranscript: ({ full, current }) => {
        const display = [full, current && !full.endsWith(current) ? current : ""].filter(Boolean).join(" ").trim() || current;
        this.liveTranscript = display;
        onInterimTranscript?.(display);
      },
    });
  }

  async stop() {
    if (this.captioner) {
      const capText = this.captioner.getTranscript();
      if (capText && !this.liveTranscript) this.liveTranscript = capText;
      this.captioner.stop();
      this.captioner = null;
    }
    const sampleRate = this.ctx?.sampleRate || 48000;
    if (this.processor) {
      try {
        this.processor.disconnect();
      } catch {
        // ignore
      }
      this.processor = null;
    }
    if (this.muteGain) {
      try {
        this.muteGain.disconnect();
      } catch {
        // ignore
      }
      this.muteGain = null;
    }
    if (this.stream) this.stream.getTracks().forEach((t) => t.stop());
    if (this.ctx) await this.ctx.close().catch(() => {});
    this.stream = null;
    this.ctx = null;

    const totalLen = this.chunks.reduce((acc, c) => acc + c.length, 0);
    const merged = new Float32Array(totalLen);
    let offset = 0;
    for (const c of this.chunks) {
      merged.set(c, offset);
      offset += c.length;
    }
    const downsampled = downsampleBuffer(merged, sampleRate, 16000);
    const wavBlob = encodeWav16k(downsampled, 16000);

    return {
      wavBlob,
      browserTranscript: this.liveTranscript.trim(),
      durationMs: Math.round((downsampled.length / 16000) * 1000),
    };
  }
}

export function playReplyAudio({ audioBase64, audioMimeType, text, language = "hi-IN" }) {
  if (audioBase64) {
    try {
      const audio = new Audio(`data:${audioMimeType || "audio/wav"};base64,${audioBase64}`);
      audio.play().catch(() => { });
      return;
    } catch {
      // fallback to browser speechSynthesis
    }
  }
  if (text && "speechSynthesis" in window) {
    try {
      window.speechSynthesis.cancel();
      const utter = new SpeechSynthesisUtterance(text);
      utter.lang = language;
      utter.rate = 1.02;
      window.speechSynthesis.speak(utter);
    } catch {
      // ignore if speechSynthesis blocked
    }
  }
}

