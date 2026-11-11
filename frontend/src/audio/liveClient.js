/**
 * Real-time Gemini 3.8 Live WebSocket client:
 * - Immediate click-activated getUserMedia + AudioContext.resume() -> AudioWorklet (with ScriptProcessor fallback)
 * - 85Hz high-pass noise filter + 2.2x mic pre-gain + dynamics compressor -> 16kHz Int16 binary PCM frames
 * - Echo-guarded speaker playback window (prevents J.A.R.V.I.S. greeting from interrupting itself)
 * - Separate playback AudioContext for jitter-free 24kHz PCM responses & barge-in interruption clearing
 */

const WORKLET_PROCESSOR_CODE = `
class PcmCaptureProcessor extends AudioWorkletProcessor {
  constructor() {
    super();
    this._buf = new Float32Array(1600);
    this._offset = 0;
  }
  process(inputs) {
    const ch = inputs[0]?.[0];
    if (!ch || ch.length === 0) return true;
    let i = 0;
    while (i < ch.length) {
      const take = Math.min(ch.length - i, this._buf.length - this._offset);
      this._buf.set(ch.subarray(i, i + take), this._offset);
      this._offset += take;
      i += take;
      if (this._offset >= this._buf.length) {
        this.port.postMessage(this._buf.slice(0));
        this._offset = 0;
      }
    }
    return true;
  }
}
registerProcessor("pcm-capture-processor", PcmCaptureProcessor);
`;

function downsampleToInt16(float32, inputRate, targetRate = 16000) {
  if (targetRate >= inputRate) {
    const out = new Int16Array(float32.length);
    for (let i = 0; i < float32.length; i += 1) {
      const s = Math.max(-1, Math.min(1, float32[i]));
      out[i] = s < 0 ? s * 0x8000 : s * 0x7fff;
    }
    return out;
  }
  const ratio = inputRate / targetRate;
  const newLength = Math.max(1, Math.floor(float32.length / ratio));
  const out = new Int16Array(newLength);
  let outIdx = 0;
  let inOffset = 0;
  while (outIdx < newLength) {
    const nextOffset = Math.min(float32.length, Math.round((outIdx + 1) * ratio));
    let sum = 0;
    let count = 0;
    for (let i = inOffset; i < nextOffset; i += 1) {
      sum += float32[i];
      count += 1;
    }
    const sample = count > 0 ? Math.max(-1, Math.min(1, sum / count)) : 0;
    out[outIdx] = sample < 0 ? sample * 0x8000 : sample * 0x7fff;
    outIdx += 1;
    inOffset = nextOffset;
  }
  return out;
}

export class GeminiLiveClient {
  constructor() {
    this.ws = null;
    this.wsReady = false;
    this.captureCtx = null;
    this.playbackCtx = null;
    this.stream = null;
    this.workletNode = null;
    this.processor = null;
    this.muteGain = null;
    this.playCursor = 0;
    this.activeSources = [];
    this.pingTimer = null;
    this.resumeHandle = null;
    this.shouldStayConnected = false;
    this.micActive = false;
    this.callbacks = {};
    this.connectParams = null;

    // Hybrid VAD, echo-window & signal diagnostics
    this.aiSpeaking = false;
    this.aiStoppedAt = 0;
    this.speechDetectedInTurn = false;
    this.speechFrameCount = 0;
    this.lastSpeechAt = 0;
    this.turnEndSent = false;
    this.noiseFloor = 0.0025;
    this.maxRmsObserved = 0;
    this.listeningStartedAt = 0;
    this.lastUiUpdateAt = 0;
    this.signalOk = null;
    this.deviceLabel = "";
  }

  /**
   * Must be called inside the click gesture so AudioContext capture & playback start unlocked.
   */
  async prepareMic({ onLevel, onMicInfo } = {}) {
    if (onLevel) this.callbacks.onLevel = onLevel;
    if (onMicInfo) this.callbacks.onMicInfo = onMicInfo;

    const AudioCtx = window.AudioContext || window.webkitAudioContext;
    if (!this.playbackCtx) {
      this.playbackCtx = new AudioCtx();
    }
    if (this.playbackCtx.state === "suspended") {
      await this.playbackCtx.resume().catch(() => {});
    }

    if (this.micActive && this.stream && this.captureCtx) {
      if (this.captureCtx.state === "suspended") {
        await this.captureCtx.resume().catch(() => {});
      }
      return;
    }

    if (!navigator.mediaDevices?.getUserMedia) {
      throw new Error("Microphone capture requires localhost/HTTPS browser permission.");
    }

    this.captureCtx = new AudioCtx();
    if (this.captureCtx.state === "suspended") {
      await this.captureCtx.resume().catch(() => {});
    }

    this.stream = await navigator.mediaDevices.getUserMedia({
      audio: {
        channelCount: 1,
        echoCancellation: true,
        noiseSuppression: true,
        autoGainControl: true,
      },
    });
    if (this.captureCtx.state === "suspended") {
      await this.captureCtx.resume().catch(() => {});
    }

    const track = this.stream.getAudioTracks()[0];
    this.deviceLabel = track?.label || "Default Microphone";
    this.signalOk = null;
    this.maxRmsObserved = 0;
    this.speechDetectedInTurn = false;
    this.speechFrameCount = 0;
    this.turnEndSent = false;
    this.listeningStartedAt = performance.now();
    this.micActive = true;

    this.callbacks.onMicInfo?.({
      deviceLabel: this.deviceLabel,
      signalOk: null,
      speechActive: false,
    });

    const src = this.captureCtx.createMediaStreamSource(this.stream);

    // 85Hz high-pass filter cuts street traffic rumble & shop motor hum
    const highPass = this.captureCtx.createBiquadFilter();
    highPass.type = "highpass";
    highPass.frequency.value = 85;

    // Soft 2.2x pre-gain + gentle compressor lifts quiet laptop mics without clipping loud shop noise
    const preGain = this.captureCtx.createGain();
    preGain.gain.value = 2.2;

    const compressor = this.captureCtx.createDynamicsCompressor();
    compressor.threshold.value = -30;
    compressor.knee.value = 24;
    compressor.ratio.value = 6;
    compressor.attack.value = 0.004;
    compressor.release.value = 0.15;

    this.muteGain = this.captureCtx.createGain();
    this.muteGain.gain.value = 0.0;

    src.connect(highPass);
    highPass.connect(preGain);
    preGain.connect(compressor);

    let usingWorklet = false;
    if (this.captureCtx.audioWorklet) {
      try {
        const blobUrl = URL.createObjectURL(
          new Blob([WORKLET_PROCESSOR_CODE], { type: "application/javascript" })
        );
        try {
          await this.captureCtx.audioWorklet.addModule(blobUrl);
        } finally {
          URL.revokeObjectURL(blobUrl);
        }
        const worklet = new AudioWorkletNode(this.captureCtx, "pcm-capture-processor");
        worklet.port.onmessage = (evt) => {
          if (evt.data) this._handleAudioFrame(evt.data);
        };
        compressor.connect(worklet);
        worklet.connect(this.muteGain);
        this.workletNode = worklet;
        usingWorklet = true;
      } catch {
        usingWorklet = false;
      }
    }

    if (!usingWorklet) {
      const processor = this.captureCtx.createScriptProcessor(2048, 1, 1);
      processor.onaudioprocess = (e) => {
        this._handleAudioFrame(e.inputBuffer.getChannelData(0));
      };
      compressor.connect(processor);
      processor.connect(this.muteGain);
      this.processor = processor;
    }

    this.muteGain.connect(this.captureCtx.destination);
  }

  _handleAudioFrame(f32) {
    if (!this.micActive || !this.captureCtx) return;

    let sum = 0;
    for (let i = 0; i < f32.length; i += 1) sum += f32[i] * f32[i];
    const rms = Math.sqrt(sum / f32.length);
    const now = performance.now();

    const speakerWindow = this.aiSpeaking || now - this.aiStoppedAt < 120;
    if (!speakerWindow && rms > this.maxRmsObserved) {
      this.maxRmsObserved = rms;
    }

    const speechThreshold = Math.max(0.006, Math.min(0.024, this.noiseFloor * 2.2));
    const strongBargeIn = speakerWindow && rms >= 0.065;
    const isSpeech = speakerWindow ? strongBargeIn : rms > speechThreshold;

    if (!speakerWindow) {
      if (rms > 0.00025 && this.signalOk !== true) {
        this.signalOk = true;
        this.callbacks.onMicInfo?.({
          deviceLabel: this.deviceLabel,
          signalOk: true,
          speechActive: isSpeech,
        });
      } else if (
        this.signalOk === null &&
        now - this.listeningStartedAt > 3500 &&
        this.maxRmsObserved < 0.00025
      ) {
        this.signalOk = false;
        this.callbacks.onMicInfo?.({
          deviceLabel: this.deviceLabel,
          signalOk: false,
          speechActive: false,
        });
      }
    }

    if (now - this.lastUiUpdateAt >= 85) {
      this.lastUiUpdateAt = now;
      this.callbacks.onLevel?.(speakerWindow && !strongBargeIn ? 0 : Math.min(1, rms * 10));
      if (!speakerWindow && this.signalOk === true) {
        this.callbacks.onMicInfo?.({
          deviceLabel: this.deviceLabel,
          signalOk: true,
          speechActive: isSpeech,
        });
      }
    }

    if (!this.ws || this.ws.readyState !== WebSocket.OPEN || !this.wsReady) {
      return;
    }

    if (speakerWindow && !strongBargeIn) {
      // Keep 16kHz audio clock alive with quiet zero PCM while J.A.R.V.I.S. speaks
      const silLen = Math.max(1, Math.floor((f32.length * 16000) / this.captureCtx.sampleRate));
      this.ws.send(new Int16Array(silLen).buffer);
      return;
    }

    if (isSpeech) {
      this.speechFrameCount += 1;
      if (this.speechFrameCount >= 3) {
        this.speechDetectedInTurn = true;
        this.lastSpeechAt = now;
        this.turnEndSent = false;
      }
    } else if (!this.speechDetectedInTurn) {
      this.speechFrameCount = 0;
      this.noiseFloor = this.noiseFloor * 0.94 + rms * 0.06;
    }

    const pcm16 = downsampleToInt16(f32, this.captureCtx.sampleRate, 16000);
    this.ws.send(pcm16.buffer);

    if (this.speechDetectedInTurn && !this.turnEndSent && now - this.lastSpeechAt >= 950) {
      this.turnEndSent = true;
      this.speechDetectedInTurn = false;
      this.speechFrameCount = 0;
      this.ws.send(JSON.stringify({ type: "end_turn" }));
    }
  }

  connect({
    sessionId,
    language,
    extended = false,
    interactiveConfirm = true,
    greet = true,
    voice = "Charon",
    resumeHandle = null,
    onReady,
    onLedgerEvent,
    onText,
    onLevel,
    onMicInfo,
    onTurnComplete,
    onSpeakingChange,
    onError,
    onClose,
  }) {
    this.shouldStayConnected = true;
    this.wsReady = false;
    if (resumeHandle) this.resumeHandle = resumeHandle;
    this.connectParams = { sessionId, language, extended, interactiveConfirm, greet, voice };
    this.callbacks = {
      onReady,
      onLedgerEvent,
      onText,
      onLevel,
      onMicInfo,
      onTurnComplete,
      onSpeakingChange,
      onError,
      onClose,
    };
    this._openSocket();
  }

  _openSocket() {
    if (!this.connectParams) return;
    const { sessionId, language, extended, interactiveConfirm, greet, voice } = this.connectParams;
    const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
    const qs = new URLSearchParams({
      session_id: sessionId,
      language,
      extended: extended ? "true" : "false",
      interactive_confirm: interactiveConfirm ? "true" : "false",
      greet: greet ? "true" : "false",
      voice: voice || "Charon",
    });
    if (this.resumeHandle) qs.set("resume_handle", this.resumeHandle);
    const url = `${proto}//${window.location.host}/ws/live?${qs.toString()}`;

    const ws = new WebSocket(url);
    ws.binaryType = "arraybuffer";
    this.ws = ws;

    ws.onmessage = async (evt) => {
      if (typeof evt.data !== "string") return;
      const msg = JSON.parse(evt.data);
      if (msg.type === "ready") {
        this.wsReady = true;
        this.listeningStartedAt = performance.now();
        this._startKeepalive();
        this.callbacks.onReady?.(msg);
      } else if (msg.type === "session_handle" && msg.handle) {
        this.resumeHandle = msg.handle;
      } else if (msg.type === "go_away") {
        if (this.shouldStayConnected && this.resumeHandle) {
          this._reconnectWithResume();
        }
      } else if (msg.type === "ledger_event") {
        this.callbacks.onLedgerEvent?.(msg.payload);
      } else if (msg.type === "input_transcript" || msg.type === "output_transcript" || msg.type === "text_delta") {
        this.callbacks.onText?.(msg.text, msg.type);
      } else if (msg.type === "turn_complete") {
        this.callbacks.onTurnComplete?.(msg);
      } else if (msg.type === "interrupted") {
        this._clearAudioQueue();
      } else if (msg.type === "audio_pcm") {
        this._playPcm24k(msg.data, msg.sample_rate || 24000);
      } else if (msg.type === "error") {
        // Clear stale resume handle so a broken session never loops reconnecting with the same handle
        if (!this.wsReady) {
          this.resumeHandle = null;
        }
        this.callbacks.onError?.(msg.message);
      }
    };

    ws.onerror = () => {
      if (this.shouldStayConnected) {
        this.callbacks.onError?.("Live WebSocket connection error. Retrying or switch to Push-to-Talk.");
      }
    };

    ws.onclose = () => {
      const wasReady = this.wsReady;
      this.wsReady = false;
      this._stopKeepalive();
      if (this.shouldStayConnected && this.resumeHandle && wasReady) {
        this._reconnectWithResume();
        return;
      }
      this.resumeHandle = null;
      this.shouldStayConnected = false;
      this.stopMicCapture();
      this.callbacks.onClose?.();
    };
  }

  _reconnectWithResume() {
    this.wsReady = false;
    this._stopKeepalive();
    if (this.ws) {
      const old = this.ws;
      this.ws = null;
      old.onclose = null;
      try {
        old.close();
      } catch {
        // ignore
      }
    }
    setTimeout(() => {
      if (this.shouldStayConnected) this._openSocket();
    }, 250);
  }

  _startKeepalive() {
    this._stopKeepalive();
    this.pingTimer = setInterval(() => {
      if (this.ws && this.ws.readyState === WebSocket.OPEN) {
        this.ws.send(JSON.stringify({ type: "ping" }));
      }
    }, 15000);
  }

  _stopKeepalive() {
    if (this.pingTimer) {
      clearInterval(this.pingTimer);
      this.pingTimer = null;
    }
  }

  async startMicStream() {
    await this.prepareMic();
  }

  sendText(text) {
    if (this.ws && this.ws.readyState === WebSocket.OPEN && text.trim()) {
      this._clearAudioQueue();
      this.ws.send(JSON.stringify({ type: "text", text: text.trim() }));
      return true;
    }
    return false;
  }

  finishTurn() {
    if (this.ws && this.ws.readyState === WebSocket.OPEN) {
      this.ws.send(JSON.stringify({ type: "end_turn" }));
    }
  }

  stopMicCapture() {
    this.micActive = false;
    this.callbacks.onLevel?.(0);
    if (this.workletNode) {
      try {
        this.workletNode.port.onmessage = null;
        this.workletNode.disconnect();
      } catch {
        // ignore
      }
      this.workletNode = null;
    }
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
    if (this.stream) {
      this.stream.getTracks().forEach((t) => t.stop());
      this.stream = null;
    }
    if (this.captureCtx) {
      this.captureCtx.close().catch(() => { });
      this.captureCtx = null;
    }
  }

  _playPcm24k(base64Data, sampleRate = 24000) {
    if (!this.playbackCtx) {
      const AudioCtx = window.AudioContext || window.webkitAudioContext;
      this.playbackCtx = new AudioCtx();
    }
    if (this.playbackCtx.state === "suspended") {
      this.playbackCtx.resume().catch(() => { });
    }
    const bin = atob(base64Data);
    const bytes = new Uint8Array(bin.length);
    for (let i = 0; i < bin.length; i += 1) bytes[i] = bin.charCodeAt(i);
    const pcm16 = new Int16Array(bytes.buffer);
    const f32 = new Float32Array(pcm16.length);
    for (let i = 0; i < pcm16.length; i += 1) f32[i] = pcm16[i] / 32768;

    const buf = this.playbackCtx.createBuffer(1, f32.length, sampleRate);
    buf.copyToChannel(f32, 0);
    const src = this.playbackCtx.createBufferSource();
    src.buffer = buf;
    src.connect(this.playbackCtx.destination);
    const startAt = Math.max(this.playbackCtx.currentTime, this.playCursor);
    src.start(startAt);
    this.playCursor = startAt + buf.duration;
    this.activeSources.push(src);
    if (!this.aiSpeaking) {
      this.aiSpeaking = true;
      this.callbacks.onSpeakingChange?.(true);
    }
    src.onended = () => {
      this.activeSources = this.activeSources.filter((s) => s !== src);
      if (this.activeSources.length === 0) {
        this.aiSpeaking = false;
        this.aiStoppedAt = performance.now();
        this.listeningStartedAt = performance.now();
        this.callbacks.onSpeakingChange?.(false);
      }
    };
  }

  _clearAudioQueue() {
    for (const src of this.activeSources) {
      try {
        src.stop();
      } catch {
        // already stopped
      }
    }
    this.activeSources = [];
    this.playCursor = this.playbackCtx ? this.playbackCtx.currentTime : 0;
    this.aiSpeaking = false;
    this.aiStoppedAt = performance.now();
    this.callbacks.onSpeakingChange?.(false);
  }

  stop() {
    this.shouldStayConnected = false;
    this.wsReady = false;
    this.resumeHandle = null;
    this._stopKeepalive();
    this._clearAudioQueue();
    this.stopMicCapture();
    if (this.ws) {
      if (this.ws.readyState === WebSocket.OPEN) {
        try {
          this.ws.send(JSON.stringify({ type: "end_turn" }));
        } catch {
          // ignore
        }
      }
      this.ws.onclose = null;
      try {
        this.ws.close();
      } catch {
        // ignore
      }
      this.ws = null;
    }
    if (this.playbackCtx) {
      this.playbackCtx.close().catch(() => { });
      this.playbackCtx = null;
    }
  }
}
