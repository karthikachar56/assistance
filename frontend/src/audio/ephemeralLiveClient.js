import { api } from "../api/client";

/**
 * Browser-direct Gemini 3.8 Live client over short-lived Ephemeral Tokens.
 * Enables true real-time bidirectional voice + tool execution on serverless hosts (e.g. Vercel)
 * without requiring a stateful backend /ws/live proxy.
 */

const WORKLET_PROCESSOR_CODE = `
class EphemeralPcmCaptureProcessor extends AudioWorkletProcessor {
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
registerProcessor("ephemeral-pcm-capture-processor", EphemeralPcmCaptureProcessor);
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

function int16ToBase64(int16) {
  const bytes = new Uint8Array(int16.buffer, int16.byteOffset, int16.byteLength);
  let bin = "";
  const chunkSize = 0x8000;
  for (let i = 0; i < bytes.length; i += chunkSize) {
    bin += String.fromCharCode(...bytes.subarray(i, i + chunkSize));
  }
  return btoa(bin);
}

async function decodeWsJson(data) {
  if (typeof data === "string") return JSON.parse(data);
  if (data instanceof Blob) return JSON.parse(await data.text());
  if (data instanceof ArrayBuffer) return JSON.parse(new TextDecoder().decode(data));
  return null;
}

export class EphemeralGeminiLiveClient {
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
    this.resumeHandle = null;
    this.shouldStayConnected = false;
    this.micActive = false;
    this.callbacks = {};
    this.connectParams = null;
    this.pendingAction = null;
    this.lastCommittedTxIds = [];
    this.activeModel = "gemini-3.8-live";

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
    const highPass = this.captureCtx.createBiquadFilter();
    highPass.type = "highpass";
    highPass.frequency.value = 85;

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
        const worklet = new AudioWorkletNode(this.captureCtx, "ephemeral-pcm-capture-processor");
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
      processor.onaudioprocess = (evt) => {
        this._handleAudioFrame(evt.inputBuffer.getChannelData(0));
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
    for (let i = 0; i < f32.length; i += 1) {
      sum += f32[i] * f32[i];
    }
    const rms = Math.sqrt(sum / f32.length);
    const now = performance.now();

    const speakerWindow = this.aiSpeaking || now - this.aiStoppedAt < 380;
    const speechThreshold = Math.max(0.014, this.noiseFloor * 2.4);
    const isSpeech = rms > speechThreshold;
    const strongBargeIn = rms > Math.max(0.055, this.noiseFloor * 4.5);

    if (speakerWindow && strongBargeIn && this.aiSpeaking) {
      this._clearAudioQueue();
    }

    if (!speakerWindow) {
      if (rms > this.maxRmsObserved) this.maxRmsObserved = rms;
      if (rms > 0.002 && this.signalOk !== true) {
        this.signalOk = true;
        this.callbacks.onMicInfo?.({
          deviceLabel: this.deviceLabel,
          signalOk: true,
          speechActive: isSpeech,
        });
      } else if (
        this.signalOk === null &&
        this.wsReady &&
        now - this.listeningStartedAt > 3000 &&
        this.maxRmsObserved < 0.0012
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
      const silLen = Math.max(1, Math.floor((f32.length * 16000) / this.captureCtx.sampleRate));
      const silB64 = int16ToBase64(new Int16Array(silLen));
      this.ws.send(
        JSON.stringify({
          realtimeInput: { audio: { data: silB64, mimeType: "audio/pcm;rate=16000" } },
        })
      );
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
    const b64 = int16ToBase64(pcm16);
    this.ws.send(
      JSON.stringify({
        realtimeInput: { audio: { data: b64, mimeType: "audio/pcm;rate=16000" } },
      })
    );

    if (this.speechDetectedInTurn && !this.turnEndSent && now - this.lastSpeechAt >= 950) {
      this.turnEndSent = true;
      this.speechDetectedInTurn = false;
      this.speechFrameCount = 0;
      this.ws.send(JSON.stringify({ realtimeInput: { audioStreamEnd: true } }));
    }
  }

  async connect({
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
    await this._openSocket();
  }

  async _openSocket() {
    if (!this.connectParams || !this.shouldStayConnected) return;
    const { sessionId, language, extended, interactiveConfirm, greet, voice } = this.connectParams;

    let tokenData;
    try {
      tokenData = await api.liveToken({
        session_id: sessionId,
        language,
        extended,
        interactive_confirm: interactiveConfirm,
        voice: voice || "Charon",
        resume_handle: this.resumeHandle,
      });
    } catch (err) {
      if (this.shouldStayConnected) {
        this.callbacks.onError?.(err.message || "Could not start Live Voice session.");
      }
      this.stop();
      this.callbacks.onClose?.();
      return;
    }

    if (!this.shouldStayConnected) return;
    this.activeModel = tokenData.model || "gemini-3.8-live";

    const ws = new WebSocket(tokenData.ws_url);
    this.ws = ws;

    ws.onopen = () => {
      if (ws !== this.ws) return;
      ws.send(JSON.stringify({ setup: tokenData.setup }));
    };

    ws.onmessage = async (evt) => {
      let msg;
      try {
        msg = await decodeWsJson(evt.data);
      } catch {
        return;
      }
      if (!msg) return;

      if (msg.setupComplete !== undefined) {
        this.wsReady = true;
        this.listeningStartedAt = performance.now();
        this.callbacks.onReady?.({ model: this.activeModel, transport: "ephemeral" });
        if (greet && !this.resumeHandle && tokenData.greet_prompt) {
          ws.send(JSON.stringify({ realtimeInput: { text: tokenData.greet_prompt } }));
        }
        return;
      }

      if (msg.sessionResumptionUpdate?.newHandle) {
        this.resumeHandle = msg.sessionResumptionUpdate.newHandle;
      }

      if (msg.goAway) {
        if (this.shouldStayConnected && this.resumeHandle) {
          this._reconnectWithResume();
        }
      }

      if (msg.toolCall?.functionCalls?.length) {
        const functionResponses = [];
        for (const fc of msg.toolCall.functionCalls) {
          try {
            const res = await api.liveTool({
              session_id: sessionId,
              language,
              model: this.activeModel,
              tool_name: fc.name,
              args: fc.args || {},
              interactive_confirm: interactiveConfirm,
              pending_action: this.pendingAction,
              last_committed_tx_ids: this.lastCommittedTxIds,
            });
            this.pendingAction = res.pending_action || null;
            if (Array.isArray(res.transactions) && res.transactions.length > 0) {
              this.lastCommittedTxIds = res.transactions.map((t) => t.id);
            }
            this.callbacks.onLedgerEvent?.(res);
            functionResponses.push({
              id: fc.id,
              name: fc.name,
              response: res,
            });
          } catch (err) {
            functionResponses.push({
              id: fc.id,
              name: fc.name,
              response: { status: "error", spoken_reply: err.message || "Ledger tool error." },
            });
          }
        }
        if (ws.readyState === WebSocket.OPEN) {
          ws.send(JSON.stringify({ toolResponse: { functionResponses } }));
        }
      }

      const sc = msg.serverContent;
      if (sc) {
        if (sc.interrupted) {
          this._clearAudioQueue();
        }
        if (sc.inputTranscription?.text) {
          this.callbacks.onText?.(sc.inputTranscription.text, "input_transcript");
        }
        if (sc.outputTranscription?.text) {
          this.callbacks.onText?.(sc.outputTranscription.text, "output_transcript");
        }
        const parts = sc.modelTurn?.parts || [];
        for (const part of parts) {
          const b64 = part.inlineData?.data;
          if (b64) {
            this._playPcm24k(b64, 24000);
          }
          if (part.text) {
            this.callbacks.onText?.(part.text, "text_delta");
          }
        }
        if (sc.turnComplete) {
          this.callbacks.onTurnComplete?.({});
        }
      }
    };

    ws.onerror = () => {
      if (this.shouldStayConnected) {
        this.callbacks.onError?.("Direct Gemini Live connection error. Switching cleanly if needed.");
      }
    };

    ws.onclose = (evt) => {
      const wasReady = this.wsReady;
      this.wsReady = false;
      if (this.shouldStayConnected && this.resumeHandle && wasReady) {
        setTimeout(() => this._reconnectWithResume(), 350);
        return;
      }
      if (this.shouldStayConnected && !wasReady) {
        this.callbacks.onError?.(
          evt?.reason || "Gemini Live session closed before setup completed. Push-to-Talk stays ready."
        );
      }
      this.stop();
      this.callbacks.onClose?.();
    };
  }

  async _reconnectWithResume() {
    if (!this.shouldStayConnected) return;
    if (this.ws) {
      try {
        this.ws.onclose = null;
        this.ws.close();
      } catch {
        // ignore
      }
      this.ws = null;
    }
    this.wsReady = false;
    await this._openSocket();
  }

  sendText(text) {
    if (this.ws && this.ws.readyState === WebSocket.OPEN && this.wsReady) {
      this.ws.send(JSON.stringify({ realtimeInput: { text } }));
      return true;
    }
    return false;
  }

  _clearAudioQueue() {
    for (const src of this.activeSources) {
      try {
        src.stop();
      } catch {
        // ignore
      }
    }
    this.activeSources = [];
    if (this.playbackCtx) {
      this.playCursor = this.playbackCtx.currentTime;
    }
    if (this.aiSpeaking) {
      this.aiSpeaking = false;
      this.aiStoppedAt = performance.now();
      this.callbacks.onSpeakingChange?.(false);
    }
  }

  _playPcm24k(base64Data, sampleRate = 24000) {
    if (!this.playbackCtx) {
      const AudioCtx = window.AudioContext || window.webkitAudioContext;
      this.playbackCtx = new AudioCtx();
    }
    if (this.playbackCtx.state === "suspended") {
      this.playbackCtx.resume().catch(() => {});
    }

    const bin = atob(base64Data);
    const bytes = new Uint8Array(bin.length);
    for (let i = 0; i < bin.length; i += 1) {
      bytes[i] = bin.charCodeAt(i);
    }
    const view = new DataView(bytes.buffer);
    const numSamples = Math.floor(bytes.byteLength / 2);
    if (numSamples === 0) return;

    const f32 = new Float32Array(numSamples);
    for (let i = 0; i < numSamples; i += 1) {
      f32[i] = view.getInt16(i * 2, true) / 32768.0;
    }

    const buf = this.playbackCtx.createBuffer(1, numSamples, sampleRate);
    buf.getChannelData(0).set(f32);

    const src = this.playbackCtx.createBufferSource();
    src.buffer = buf;
    src.connect(this.playbackCtx.destination);

    const now = this.playbackCtx.currentTime;
    if (this.playCursor < now + 0.02) {
      this.playCursor = now + 0.02;
    }
    src.start(this.playCursor);
    this.playCursor += buf.duration;
    this.activeSources.push(src);

    if (!this.aiSpeaking) {
      this.aiSpeaking = true;
      this.callbacks.onSpeakingChange?.(true);
    }

    src.onended = () => {
      this.activeSources = this.activeSources.filter((s) => s !== src);
      if (this.activeSources.length === 0 && this.aiSpeaking) {
        this.aiSpeaking = false;
        this.aiStoppedAt = performance.now();
        this.callbacks.onSpeakingChange?.(false);
      }
    };
  }

  stop() {
    this.shouldStayConnected = false;
    this.wsReady = false;
    this.micActive = false;
    this._clearAudioQueue();
    if (this.workletNode) {
      try {
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
    if (this.ws) {
      try {
        this.ws.onclose = null;
        this.ws.close();
      } catch {
        // ignore
      }
      this.ws = null;
    }
    if (this.stream) {
      this.stream.getTracks().forEach((t) => t.stop());
      this.stream = null;
    }
    if (this.captureCtx) {
      this.captureCtx.close().catch(() => {});
      this.captureCtx = null;
    }
  }
}
