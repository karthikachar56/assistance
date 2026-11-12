import React, { useEffect, useRef, useState } from "react";
import { LogOut, Mic, MicOff, PanelRightOpen, Radio, Send, User } from "lucide-react";
import {
  api,
  clearAuthToken,
  enqueueOfflineTurn,
  flushOfflineQueue,
  getAuthToken,
  getOfflineQueue,
  setAuthToken,
} from "./api/client";
import { EphemeralGeminiLiveClient } from "./audio/ephemeralLiveClient";
import { GeminiLiveClient } from "./audio/liveClient";
import { ShopAudioRecorder, playReplyAudio } from "./audio/recorder";
import { AuthGateway } from "./components/AuthPages";
import { ConsentBanner } from "./components/ConsentBanner";
import { CustomerLedgerModal } from "./components/CustomerLedgerModal";
import { CustomerLedgersScreen } from "./components/CustomerLedgersScreen";
import { CustomersScreen } from "./components/CustomersScreen";
import { DashboardScreen } from "./components/DashboardScreen";
import { JarvisHud } from "./components/JarvisHud";
import { NAV_SECTIONS, RightSlideDashboard } from "./components/RightSlideDashboard";
import { SettingsScreen } from "./components/SettingsScreen";
import { resolveInitialAuthMode } from "./utils/authHelpers";
import { previewSpokenAmount } from "./utils/numberNormalizer";
import { inferNavigationFromText } from "./utils/voiceNavigator";

function newSessionId() {
  return `khata-${Math.random().toString(36).slice(2, 8)}`;
}

export default function App() {
  const [sessionId, setSessionId] = useState(newSessionId);
  const [currentUser, setCurrentUser] = useState(null);
  const [authMode, setAuthMode] = useState(() =>
    typeof window !== "undefined"
      ? resolveInitialAuthMode(window.location.pathname, window.location.hash)
      : "login"
  );
  const [authChecking, setAuthChecking] = useState(() => Boolean(getAuthToken()));
  const [authSubmitting, setAuthSubmitting] = useState(false);
  const [authError, setAuthError] = useState("");

  const [tab, setTab] = useState("home");
  const [drawerOpen, setDrawerOpen] = useState(false);

  useEffect(() => {
    setDrawerOpen(false);
  }, [tab]);

  function navigateToTab(nextTab) {
    setTab(nextTab);
    setDrawerOpen(false);
    refreshAll();
  }
  const [statusInfo, setStatusInfo] = useState(null);
  const [dashboard, setDashboard] = useState(null);
  const [customers, setCustomers] = useState([]);
  const [allTransactions, setAllTransactions] = useState([]);
  const [focusLedgersCustId, setFocusLedgersCustId] = useState(null);
  const [lastTurn, setLastTurn] = useState(null);
  const [selectedCustomer, setSelectedCustomer] = useState(null);
  const [selectedCustomerTxs, setSelectedCustomerTxs] = useState([]);

  const [language, setLanguage] = useState("hi-IN");
  const [aiMode, setAiMode] = useState("cascaded");
  const [speechProvider, setSpeechProvider] = useState("auto");
  const [ephemeral, setEphemeral] = useState(false);
  const [consentAccepted, setConsentAccepted] = useState(true);
  const [useDirectAudio, setUseDirectAudio] = useState(false);
  const [useLiveStream, setUseLiveStream] = useState(false);
  const [interactiveConfirm, setInteractiveConfirm] = useState(true);

  const [textDraft, setTextDraft] = useState("");
  const [isRecording, setIsRecording] = useState(false);
  const [isProcessing, setIsProcessing] = useState(false);
  const [liveConnected, setLiveConnected] = useState(false);
  const [liveConnecting, setLiveConnecting] = useState(false);
  const [aiSpeaking, setAiSpeaking] = useState(false);
  const [liveUserText, setLiveUserText] = useState("");
  const [liveTranscriptNote, setLiveTranscriptNote] = useState("");
  const [liveMetrics, setLiveMetrics] = useState(null);
  const [micInfo, setMicInfo] = useState({ deviceLabel: "", signalOk: null, speechActive: false });
  const [conversationFeed, setConversationFeed] = useState([]);
  const [micLevel, setMicLevel] = useState(0);
  const [online, setOnline] = useState(() => navigator.onLine);
  const [offlineCount, setOfflineCount] = useState(() => getOfflineQueue().length);
  const [errorMsg, setErrorMsg] = useState("");

  const [liveHighlightTxIds, setLiveHighlightTxIds] = useState([]);
  const [liveHighlightCustId, setLiveHighlightCustId] = useState(null);

  const recorderRef = useRef(null);
  const liveClientRef = useRef(null);
  const lastTurnRef = useRef(null);
  const liveAiAccumRef = useRef("");
  const liveUserAccumRef = useRef("");
  const initializedAudioPrefRef = useRef(false);
  const customersRef = useRef([]);
  const selectedCustomerIdRef = useRef(null);
  const tabRef = useRef("home");
  const turnNavigatedRef = useRef(false);
  const highlightTimerRef = useRef(null);
  const hasLoadedPrimarySettingsRef = useRef(false);
  const silenceTimerRef = useRef(null);
  const pttSpeechDetectedRef = useRef(false);
  const pttStoppingRef = useRef(false);
  const runtimeStateRef = useRef({});
  runtimeStateRef.current = { language, aiMode, interactiveConfirm, useDirectAudio, statusInfo };

  const isVercelHost =
    typeof window !== "undefined" && /(?:^|\.)vercel\.app$/i.test(window.location.hostname);
  const liveWsSupported = Boolean(!isVercelHost && statusInfo?.live_ws_supported === true);
  const liveEphemeralSupported = Boolean(statusInfo?.gemini_configured && statusInfo?.live_ephemeral_supported);
  const canLiveStream = Boolean(statusInfo?.gemini_configured && (liveWsSupported || liveEphemeralSupported));

  function updateLastTurn(turn) {
    lastTurnRef.current = turn;
    setLastTurn(turn);
  }

  useEffect(() => {
    tabRef.current = tab;
  }, [tab]);

  function scheduleClearHighlights() {
    if (highlightTimerRef.current) clearTimeout(highlightTimerRef.current);
    highlightTimerRef.current = setTimeout(() => {
      setLiveHighlightTxIds([]);
      setLiveHighlightCustId(null);
    }, 7000);
  }

  function appendConversation(role, text) {
    const clean = (text || "").trim();
    if (!clean) return;
    setConversationFeed((prev) => {
      const next = [...prev, { id: `${Date.now()}-${Math.random().toString(36).slice(2, 6)}`, role, text: clean }];
      return next.slice(-8);
    });
  }

  async function refreshAll(searchQuery = "") {
    try {
      const [st, dash, custs, allTxs] = await Promise.all([
        api.status(),
        api.dashboard(),
        api.customers(searchQuery),
        api.transactions(),
      ]);
      setStatusInfo(st);
      setDashboard(dash);
      setCustomers(custs);
      setAllTransactions(allTxs);
      customersRef.current = custs;

      if (selectedCustomerIdRef.current != null) {
        const freshCust = custs.find((c) => c.id === selectedCustomerIdRef.current);
        if (freshCust) {
          setSelectedCustomer(freshCust);
          const txs = await api.customerTransactions(freshCust.id);
          setSelectedCustomerTxs(txs);
        }
      }

      const s = st.settings || {};
      const isFallbackContainer = Boolean(st.storage_fallback_reason);
      if (!isFallbackContainer) {
        hasLoadedPrimarySettingsRef.current = true;
      }
      if (!hasLoadedPrimarySettingsRef.current || !isFallbackContainer) {
        if (s.preferred_language) setLanguage(s.preferred_language);
        if (s.ai_mode) setAiMode(s.ai_mode);
        if (s.speech_provider) setSpeechProvider(s.speech_provider);
        setEphemeral(s.ephemeral_transcripts === "true");
        setConsentAccepted(s.dpdp_consent_accepted === "true");
      }
      const statusCanLive = Boolean(
        st.gemini_configured && ((!isVercelHost && st.live_ws_supported === true) || st.live_ephemeral_supported)
      );
      if (!initializedAudioPrefRef.current) {
        initializedAudioPrefRef.current = true;
        if (st.gemini_configured || st.gnani_configured) {
          setUseDirectAudio(true);
        }
        setUseLiveStream(statusCanLive);
      } else if (!statusCanLive) {
        stopLiveSession();
        setUseLiveStream(false);
      }
      setErrorMsg("");
      return { custs };
    } catch (err) {
      if (err?.status === 401) {
        clearAuthToken();
        currentUserRef.current = null;
        setCurrentUser(null);
        resetWorkspaceState();
        setAuthMode("login");
        setAuthError("Your session expired. Please sign in again.");
        return null;
      }
      const isLocalHost =
        window.location.hostname === "localhost" || window.location.hostname === "127.0.0.1";
      setErrorMsg(
        isLocalHost
          ? `Backend unreachable (${err.message}). Start FastAPI on port 8000.`
          : `Backend service unavailable (${err.message}).`
      );
      return null;
    }
  }

  function resetWorkspaceState(nextUserId = null) {
    setSessionId(newSessionId());
    setDashboard(null);
    setCustomers([]);
    setAllTransactions([]);
    customersRef.current = [];
    setSelectedCustomer(null);
    setSelectedCustomerTxs([]);
    selectedCustomerIdRef.current = null;
    setLastTurn(null);
    lastTurnRef.current = null;
    setConversationFeed([]);
    setLiveHighlightTxIds([]);
    setLiveHighlightCustId(null);
    setFocusLedgersCustId(null);
    setLiveUserText("");
    setLiveTranscriptNote("");
    setTab("home");
    setDrawerOpen(false);
    hasLoadedPrimarySettingsRef.current = false;
    initializedAudioPrefRef.current = false;
    setOfflineCount(nextUserId != null ? getOfflineQueue(nextUserId).length : 0);
  }

  const currentUserRef = useRef(null);
  currentUserRef.current = currentUser;

  useEffect(() => {
    let active = true;

    async function bootstrapSession() {
      const token = getAuthToken();
      if (!token) {
        try {
          const st = await api.status();
          if (active) setStatusInfo(st);
        } catch {
          // ignore offline error on auth screen
        }
        return;
      }
      try {
        const me = await api.authMe();
        if (!active) return;
        currentUserRef.current = me;
        setCurrentUser(me);
        setOfflineCount(getOfflineQueue(me?.id).length);
        await refreshAll();
      } catch (err) {
        if (!active) return;
        if (err?.status === 401) {
          clearAuthToken();
          currentUserRef.current = null;
          setCurrentUser(null);
          setOfflineCount(0);
          setAuthMode("login");
          setAuthError("Your session expired. Please sign in again.");
        } else {
          setAuthError(err?.message || "Backend service temporarily unavailable. Please try again.");
        }
        try {
          const st = await api.status();
          if (active) setStatusInfo(st);
        } catch {
          // ignore offline error on auth screen
        }
      } finally {
        if (active) setAuthChecking(false);
      }
    }

    bootstrapSession();

    const onOnline = async () => {
      setOnline(true);
      const uid = currentUserRef.current?.id;
      if (uid == null) return;
      const res = await flushOfflineQueue(uid);
      setOfflineCount(res.remaining);
      if (res.flushed > 0) refreshAll();
    };
    const onOffline = () => setOnline(false);
    window.addEventListener("online", onOnline);
    window.addEventListener("offline", onOffline);
    return () => {
      active = false;
      window.removeEventListener("online", onOnline);
      window.removeEventListener("offline", onOffline);
      if (silenceTimerRef.current) clearTimeout(silenceTimerRef.current);
      liveClientRef.current?.stop();
    };
  }, []);

  function switchAuthMode(nextMode) {
    setAuthMode(nextMode);
    setAuthError("");
    if (typeof window !== "undefined") {
      window.history.replaceState(null, "", nextMode === "signup" ? "#/signup" : "#/login");
    }
  }

  async function completeAuthSuccess(res, preferredLanguage = null) {
    stopLiveSession();
    clearPttSilenceTimer();
    resetWorkspaceState(res.user?.id);
    setAuthToken(res.access_token);
    currentUserRef.current = res.user;
    setCurrentUser(res.user);
    if (preferredLanguage) {
      setLanguage(preferredLanguage);
      try {
        await api.updateSettings({ preferred_language: preferredLanguage });
      } catch {
        // non-fatal if settings update fails
      }
    }
    if (typeof window !== "undefined" && (window.location.hash === "#/login" || window.location.hash === "#/signup" || window.location.pathname === "/login" || window.location.pathname === "/signup")) {
      window.history.replaceState(null, "", "/");
    }
    await refreshAll();
  }

  async function handleLogin(payload) {
    setAuthSubmitting(true);
    setAuthError("");
    try {
      const res = await api.authLogin(payload);
      await completeAuthSuccess(res);
    } catch (err) {
      setAuthError(err.message || "Unable to sign in.");
    } finally {
      setAuthSubmitting(false);
    }
  }

  async function handleSignUp(payload) {
    setAuthSubmitting(true);
    setAuthError("");
    try {
      const res = await api.authSignup(payload);
      await completeAuthSuccess(res, res.user?.preferred_language || payload.preferred_language);
    } catch (err) {
      setAuthError(err.message || "Unable to create account.");
    } finally {
      setAuthSubmitting(false);
    }
  }

  async function handleLogout() {
    stopLiveSession();
    clearPttSilenceTimer();
    try {
      await api.authLogout();
    } catch {
      // ignore offline logout failure
    }
    clearAuthToken();
    currentUserRef.current = null;
    setCurrentUser(null);
    setAuthMode("login");
    setAuthError("");
    resetWorkspaceState(null);
    if (typeof window !== "undefined") {
      window.history.replaceState(null, "", "#/login");
    }
  }

  function stopLiveSession() {
    liveClientRef.current?.stop();
    liveAiAccumRef.current = "";
    liveUserAccumRef.current = "";
    setLiveConnected(false);
    setLiveConnecting(false);
    setAiSpeaking(false);
    setMicLevel(0);
    setMicInfo({ deviceLabel: "", signalOk: null, speechActive: false });
  }

  async function applyTurnUiEffects(res, fallbackTranscript = "") {
    const refreshed = await refreshAll();
    const freshCusts = refreshed?.custs || customersRef.current;

    if (res?.transactions?.length > 0) {
      const txIds = res.transactions.map((t) => t.id);
      const custId = res.transactions[0].customer_id;
      setLiveHighlightTxIds(txIds);
      setLiveHighlightCustId(custId);
      scheduleClearHighlights();
      if (tabRef.current === "settings") {
        setTab("home");
      }
    }

    if (res?.navigate_tab) {
      const nextTab = res.navigate_tab === "history" ? "home" : res.navigate_tab;
      turnNavigatedRef.current = true;
      setTab(nextTab);
      if (!res.open_customer_details) {
        selectedCustomerIdRef.current = null;
        setSelectedCustomer(null);
        setSelectedCustomerTxs([]);
      }
    }

    if (res?.open_customer_details && res?.highlight_customer_id) {
      turnNavigatedRef.current = true;
      setTab("customers");
      setLiveHighlightCustId(res.highlight_customer_id);
      scheduleClearHighlights();
      await openCustomerLedgerById(res.highlight_customer_id, freshCusts);
      return;
    }

    if (res?.highlight_customer_id) {
      setLiveHighlightCustId(res.highlight_customer_id);
      scheduleClearHighlights();
    }

    if (!res?.navigate_tab && (!res?.transactions || res.transactions.length === 0)) {
      const nav = inferNavigationFromText(fallbackTranscript || res?.transcript, freshCusts);
      if (nav) {
        turnNavigatedRef.current = true;
        setTab(nav.tab);
        if (nav.openCustomerId != null) {
          setLiveHighlightCustId(nav.openCustomerId);
          scheduleClearHighlights();
          await openCustomerLedgerById(nav.openCustomerId, freshCusts);
        } else {
          selectedCustomerIdRef.current = null;
          setSelectedCustomer(null);
          setSelectedCustomerTxs([]);
        }
      }
    }
  }

  async function runTextTurn(phrase) {
    const clean = phrase.trim();
    if (!clean) return;
    setLiveUserText(clean);
    if (useLiveStream && canLiveStream && liveConnected && liveClientRef.current?.sendText(clean)) {
      appendConversation("user", clean);
      setTextDraft("");
      return;
    }
    setIsProcessing(true);
    setErrorMsg("");
    const activePending = lastTurnRef.current?.pending_action || null;
    const activeTxIds = (lastTurnRef.current?.transactions || []).map((t) => t.id);
    const payload = {
      session_id: sessionId,
      transcript: clean,
      language,
      ai_mode: aiMode,
      include_tts: Boolean(statusInfo?.gemini_configured || statusInfo?.gnani_configured),
      interactive_confirm: interactiveConfirm,
      pending_action: activePending,
      last_committed_tx_ids: activeTxIds.length > 0 ? activeTxIds : null,
    };
    try {
      const res = await api.voiceTurnText(payload);
      updateLastTurn(res);
      setLiveTranscriptNote(res.spoken_reply);
      appendConversation("user", res.transcript);
      appendConversation("ai", res.spoken_reply);
      setTextDraft("");
      playReplyAudio({
        audioBase64: res.audio_base64,
        audioMimeType: res.audio_mime_type,
        text: res.spoken_reply,
        language,
      });
      await applyTurnUiEffects(res, clean);
    } catch (err) {
      if (!navigator.onLine) {
        const q = enqueueOfflineTurn(payload, currentUserRef.current?.id);
        setOfflineCount(q.length);
        setErrorMsg("Offline: voice transaction queued locally for auto-sync.");
      } else {
        setErrorMsg(err.message);
      }
    } finally {
      setIsProcessing(false);
    }
  }

  function clearPttSilenceTimer() {
    if (silenceTimerRef.current) {
      clearTimeout(silenceTimerRef.current);
      silenceTimerRef.current = null;
    }
  }

  function armPttSilenceTimer(delayMs = 1250) {
    clearPttSilenceTimer();
    silenceTimerRef.current = setTimeout(() => {
      if (recorderRef.current && pttSpeechDetectedRef.current && !pttStoppingRef.current) {
        finalizePushToTalkRecording();
      }
    }, delayMs);
  }

  async function finalizePushToTalkRecording() {
    if (pttStoppingRef.current || !recorderRef.current) return;
    pttStoppingRef.current = true;
    clearPttSilenceTimer();
    pttSpeechDetectedRef.current = false;
    setIsRecording(false);
    setMicLevel(0);
    const activeRecorder = recorderRef.current;
    recorderRef.current = null;
    try {
      const rt = runtimeStateRef.current;
      const result = await activeRecorder.stop();
      const canUploadAudio = rt.useDirectAudio && (rt.statusInfo?.gemini_configured || rt.statusInfo?.gnani_configured);
      if (canUploadAudio) {
        setIsProcessing(true);
        try {
          const res = await api.voiceTurnAudio({
            blob: result.wavBlob,
            sessionId,
            language: rt.language,
            aiMode: rt.aiMode,
            includeTts: true,
            fallbackTranscript: result.browserTranscript,
            interactiveConfirm: rt.interactiveConfirm,
            pendingAction: lastTurnRef.current?.pending_action || null,
            lastCommittedTxIds: (lastTurnRef.current?.transactions || []).map((t) => t.id),
          });
          updateLastTurn(res);
          setLiveUserText(res.transcript);
          setLiveTranscriptNote(res.spoken_reply);
          appendConversation("user", res.transcript);
          appendConversation("ai", res.spoken_reply);
          setTextDraft("");
          playReplyAudio({
            audioBase64: res.audio_base64,
            audioMimeType: res.audio_mime_type,
            text: res.spoken_reply,
            language: rt.language,
          });
          await applyTurnUiEffects(res, res.transcript);
        } catch (err) {
          if (result.browserTranscript) {
            await runTextTurn(result.browserTranscript);
          } else {
            setErrorMsg(err.message);
          }
        } finally {
          setIsProcessing(false);
        }
      } else if (result.browserTranscript) {
        await runTextTurn(result.browserTranscript);
      } else {
        setErrorMsg("No speech transcript detected. Check mic permission or type your command below.");
      }
    } finally {
      pttStoppingRef.current = false;
    }
  }

  async function handleReplayAudio(turn) {
    if (!turn?.spoken_reply) return;
    if (turn.audio_base64) {
      playReplyAudio({
        audioBase64: turn.audio_base64,
        audioMimeType: turn.audio_mime_type,
        text: turn.spoken_reply,
        language,
      });
      return;
    }
    if (statusInfo?.gemini_configured || statusInfo?.gnani_configured) {
      try {
        const ttsRes = await api.voiceTts({ text: turn.spoken_reply, language });
        if (ttsRes?.audio_base64) {
          updateLastTurn({
            ...turn,
            audio_base64: ttsRes.audio_base64,
            audio_mime_type: ttsRes.audio_mime_type || "audio/wav",
          });
          playReplyAudio({
            audioBase64: ttsRes.audio_base64,
            audioMimeType: ttsRes.audio_mime_type || "audio/wav",
            text: turn.spoken_reply,
            language,
          });
          return;
        }
      } catch {
        // fallback below to browser speechSynthesis
      }
    }
    playReplyAudio({
      audioBase64: null,
      audioMimeType: null,
      text: turn.spoken_reply,
      language,
    });
  }

  async function toggleMic() {
    if (useLiveStream && !canLiveStream) {
      stopLiveSession();
      setUseLiveStream(false);
    }
    if (useLiveStream && canLiveStream) {
      if (liveConnected || liveConnecting) {
        stopLiveSession();
        setLiveTranscriptNote("");
        await refreshAll();
      } else {
        setErrorMsg("");
        setLiveConnecting(true);
        setLiveUserText("");
        setLiveTranscriptNote("");
        liveAiAccumRef.current = "";
        liveUserAccumRef.current = "";

        const client = liveWsSupported ? new GeminiLiveClient() : new EphemeralGeminiLiveClient();
        liveClientRef.current = client;

        // Acquire microphone & unlock AudioContext synchronously inside user click gesture
        try {
          await client.prepareMic({
            onLevel: (lvl) => setMicLevel(lvl),
            onMicInfo: (info) => setMicInfo(info),
          });
        } catch (micErr) {
          stopLiveSession();
          setErrorMsg(
            `Microphone error: ${micErr.message}. Allow mic permission in Chrome/Edge and verify your Windows input device.`
          );
          return;
        }

        client.connect({
          sessionId,
          language,
          interactiveConfirm,
          greet: true,
          voice: "Charon",
          onReady: () => {
            setLiveConnecting(false);
            setLiveConnected(true);
          },
          onMicInfo: (info) => setMicInfo(info),
          onSpeakingChange: (speaking) => setAiSpeaking(speaking),
          onLedgerEvent: (payload) => {
            updateLastTurn(payload);
            if (payload.transcript) {
              setLiveUserText(payload.transcript);
            }
            if (payload.spoken_reply) {
              setLiveTranscriptNote(payload.spoken_reply);
            }
            applyTurnUiEffects(payload, payload.transcript || liveUserAccumRef.current);
          },
          onText: (txt, kind) => {
            if (!txt) return;
            if (kind === "input_transcript") {
              liveUserAccumRef.current = (liveUserAccumRef.current + " " + txt).trim();
              setLiveUserText(liveUserAccumRef.current);
              setTextDraft(liveUserAccumRef.current);
            } else if (kind === "output_transcript") {
              liveAiAccumRef.current = (liveAiAccumRef.current + " " + txt).trim();
              setLiveTranscriptNote(liveAiAccumRef.current);
            }
          },
          onLevel: (lvl) => setMicLevel(lvl),
          onTurnComplete: async (metrics) => {
            if (metrics?.ttfb_ms) setLiveMetrics(metrics);
            const finalUser = liveUserAccumRef.current;
            if (finalUser) {
              appendConversation("user", finalUser);
              liveUserAccumRef.current = "";
            }
            if (liveAiAccumRef.current) {
              appendConversation("ai", liveAiAccumRef.current);
              liveAiAccumRef.current = "";
            }
            if (!turnNavigatedRef.current && finalUser) {
              const nav = inferNavigationFromText(finalUser, customersRef.current);
              if (nav) {
                setTab(nav.tab);
                if (nav.openCustomerId != null) {
                  setLiveHighlightCustId(nav.openCustomerId);
                  scheduleClearHighlights();
                  await openCustomerLedgerById(nav.openCustomerId, customersRef.current);
                } else {
                  selectedCustomerIdRef.current = null;
                  setSelectedCustomer(null);
                  setSelectedCustomerTxs([]);
                }
              }
            }
            turnNavigatedRef.current = false;
            refreshAll();
          },
          onError: (msg) => {
            setLiveConnecting(false);
            setErrorMsg(msg);
          },
          onClose: () => {
            stopLiveSession();
          },
        });
      }
      return;
    }

    if (!isRecording) {
      try {
        clearPttSilenceTimer();
        pttSpeechDetectedRef.current = false;
        const rec = new ShopAudioRecorder();
        recorderRef.current = rec;
        setLiveUserText("");
        await rec.start({
          language,
          onLevel: (lvl) => {
            setMicLevel(lvl);
            if (lvl > 0.045) {
              pttSpeechDetectedRef.current = true;
              armPttSilenceTimer(1400);
            }
          },
          onInterimTranscript: (t) => {
            setTextDraft(t);
            setLiveUserText(t);
            if (t && t.trim()) {
              pttSpeechDetectedRef.current = true;
              armPttSilenceTimer(1250);
            }
          },
        });
        setIsRecording(true);
        setErrorMsg("");
      } catch (err) {
        setErrorMsg(`Microphone error: ${err.message}`);
      }
    } else {
      await finalizePushToTalkRecording();
    }
  }

  async function openCustomerLedgerById(id, custList = null) {
    const pool = custList || customersRef.current;
    const cust = pool.find((c) => c.id === id);
    if (!cust) return;
    selectedCustomerIdRef.current = id;
    const txs = await api.customerTransactions(id);
    setSelectedCustomer(cust);
    setSelectedCustomerTxs(txs);
  }

  async function handleUpdateSettings(patch) {
    const updated = await api.updateSettings(patch);
    if (updated.preferred_language) setLanguage(updated.preferred_language);
    if (updated.ai_mode) setAiMode(updated.ai_mode);
    if (updated.speech_provider) setSpeechProvider(updated.speech_provider);
    setEphemeral(updated.ephemeral_transcripts === "true");
    setConsentAccepted(updated.dpdp_consent_accepted === "true");
    await refreshAll();
  }

  async function handleCreateCustomer(payload) {
    const created = await api.createCustomer(payload);
    if (created?.id != null) {
      setLiveHighlightCustId(created.id);
      scheduleClearHighlights();
    }
    await refreshAll();
  }

  async function handleUpdateCustomer(id, payload) {
    await api.updateCustomer(id, payload);
    setLiveHighlightCustId(id);
    scheduleClearHighlights();
    await refreshAll();
  }

  async function handleToggleOptIn(customer) {
    await api.updateCustomer(customer.id, { reminder_opt_in: !customer.reminder_opt_in });
    await refreshAll();
  }

  async function handleManualTx(payload) {
    const tx = await api.manualTransaction(payload);
    if (tx?.id != null) {
      setLiveHighlightTxIds([tx.id]);
    }
    if (payload?.customer_id != null) {
      setLiveHighlightCustId(payload.customer_id);
    }
    scheduleClearHighlights();
    await refreshAll();
  }

  async function handleDeleteCustomer(id) {
    await api.deleteCustomer(id);
    if (selectedCustomerIdRef.current === id) {
      selectedCustomerIdRef.current = null;
      setSelectedCustomer(null);
      setSelectedCustomerTxs([]);
    }
    if (liveHighlightCustId === id) {
      setLiveHighlightCustId(null);
    }
    await refreshAll();
  }

  const detectedPreview = previewSpokenAmount(textDraft);
  const activeNavSection = NAV_SECTIONS.find((sec) => sec.id === tab) || NAV_SECTIONS[0];

  if (authChecking) {
    return (
      <div className="auth-loading-shell">
        <div className="card" style={{ textAlign: "center", padding: "24px 28px" }}>
          <div className="brand-mark" style={{ margin: "0 auto 12px" }}>
            ₹
          </div>
          <div style={{ fontWeight: 700 }}>Checking your Voice Khata session...</div>
        </div>
      </div>
    );
  }

  if (!currentUser) {
    return (
      <AuthGateway
        mode={authMode}
        onChangeMode={switchAuthMode}
        onLogin={handleLogin}
        onSignUp={handleSignUp}
        loading={authSubmitting}
        error={authError}
        statusInfo={statusInfo}
      />
    );
  }

  return (
    <div className="split-shell">
      <aside className="ai-pane" aria-label="Voice Khata AI Assistant">
        <JarvisHud
          liveConnected={liveConnected}
          liveConnecting={liveConnecting}
          aiSpeaking={aiSpeaking}
          isRecording={isRecording}
          isProcessing={isProcessing}
          useLiveStream={useLiveStream && canLiveStream}
          interactiveConfirm={interactiveConfirm}
          geminiConfigured={Boolean(statusInfo?.gemini_configured)}
          liveWsSupported={liveWsSupported}
          canLiveStream={canLiveStream}
          micLevel={micLevel}
          micInfo={micInfo}
          liveUserText={liveUserText}
          liveAiText={liveTranscriptNote}
          conversationFeed={conversationFeed}
          lastTurn={lastTurn}
          liveMetrics={liveMetrics}
          language={language}
          onToggleMic={toggleMic}
          onToggleLiveStream={(enabled) => {
            if (!enabled || !canLiveStream) {
              stopLiveSession();
              setUseLiveStream(false);
              return;
            }
            setUseLiveStream(true);
          }}
          onToggleInteractiveConfirm={(val) => {
            setInteractiveConfirm(val);
            if (liveConnected || liveConnecting) {
              stopLiveSession();
            }
          }}
          onQuickReply={runTextTurn}
          onReplayAudio={handleReplayAudio}
        />

        <div className="ai-dock-card">
          <div className="voice-dock-inner">
            <button
              className={`mic-btn ${isRecording || liveConnected ? "recording" : ""}`}
              onClick={toggleMic}
              disabled={isProcessing}
              title={
                useLiveStream && canLiveStream
                  ? "Toggle Gemini 3.8 Live Stream"
                  : "Tap to speak (auto-sends when you pause) or tap again to send now"
              }
            >
              {useLiveStream && canLiveStream ? (
                <Radio size={22} />
              ) : isRecording ? (
                <MicOff size={22} />
              ) : (
                <Mic size={22} />
              )}
            </button>

            <form
              style={{ flex: 1, minWidth: 0, display: "flex", gap: 6, alignItems: "center" }}
              onSubmit={(e) => {
                e.preventDefault();
                runTextTurn(textDraft);
              }}
            >
              <div style={{ flex: 1, minWidth: 0 }}>
                <input
                  id="khata-command-input"
                  name="command_transcript"
                  className="text-input"
                  value={textDraft}
                  onChange={(e) => setTextDraft(e.target.value)}
                  placeholder={
                    liveConnected
                      ? `Live (${Math.round(micLevel * 100)}%) listening...`
                      : isRecording
                      ? `Listening (${Math.round(micLevel * 100)}%)...`
                      : "Speak or type command..."
                  }
                />
                {detectedPreview !== null && (
                  <div style={{ fontSize: "0.7rem", color: "#fb923c", fontWeight: 700, marginTop: 3 }}>
                    Detected amount preview: ₹{detectedPreview}
                  </div>
                )}
              </div>
              <button className="btn btn-primary" type="submit" disabled={isProcessing || !textDraft.trim()}>
                <Send size={14} /> {isProcessing ? "..." : "Send"}
              </button>
            </form>
          </div>
        </div>
      </aside>

      <main className="work-pane" aria-label="Khata Workspace">
        <header className="topbar">
          <div className="brand">
            <div className="brand-mark">₹</div>
            <div>
              <h1>{currentUser?.shop_name || "Voice Khata"}</h1>
              <small>Voice Khata · Kirana AI Digital Accountant</small>
            </div>
          </div>
          <div className="topbar-right">
            <div className="topbar-user-pill" title={currentUser?.email || ""}>
              <User size={14} />
              <span>{currentUser?.name || "Shopkeeper"}</span>
            </div>
            <button
              type="button"
              className={`nav-drawer-trigger ${drawerOpen ? "open" : ""}`}
              onClick={() => setDrawerOpen((prev) => !prev)}
              aria-expanded={drawerOpen}
              aria-controls="right-sliding-dashboard"
              aria-label="Open sliding dashboard"
            >
              <PanelRightOpen size={16} />
              <span>Dashboard</span>
              <span className="nav-trigger-badge">{activeNavSection.label}</span>
            </button>
            <button
              type="button"
              className="nav-drawer-trigger"
              onClick={handleLogout}
              aria-label="Logout"
              title="Sign out"
            >
              <LogOut size={14} />
              <span>Logout</span>
            </button>
          </div>
        </header>

        {!consentAccepted && (
          <ConsentBanner
            disclosurePreview={statusInfo?.ai_disclosure_preview || "Voice Khata AI Assistant"}
            onAccept={() => handleUpdateSettings({ dpdp_consent_accepted: true })}
          />
        )}

        {errorMsg && (
          <div className="reply-banner" style={{ borderLeftColor: "#dc2626", background: "#fef2f2" }}>
            <strong>Notice:</strong> {errorMsg}
          </div>
        )}

        {tab !== "home" &&
          lastTurn &&
          (lastTurn.status === "needs_confirmation" || liveHighlightTxIds.length > 0 || liveHighlightCustId != null) && (
            <div className="reply-banner">
              <strong>{lastTurn.status === "needs_confirmation" ? "Awaiting Confirmation:" : "Live Ledger Sync:"}</strong>{" "}
              {lastTurn.spoken_reply}
            </div>
          )}

        <CustomerLedgerModal
          customer={selectedCustomer}
          transactions={selectedCustomerTxs}
          highlightTxIds={liveHighlightTxIds}
          onUpdateCustomer={handleUpdateCustomer}
          onDeleteCustomer={handleDeleteCustomer}
          onOpenInLedgers={(cust) => {
            selectedCustomerIdRef.current = null;
            setSelectedCustomer(null);
            setSelectedCustomerTxs([]);
            setFocusLedgersCustId(cust.id);
            setLiveHighlightCustId(cust.id);
            scheduleClearHighlights();
            navigateToTab("ledgers");
          }}
          onClose={() => {
            selectedCustomerIdRef.current = null;
            setSelectedCustomer(null);
            setSelectedCustomerTxs([]);
          }}
        />

        {tab === "home" && (
          <DashboardScreen
            dashboard={dashboard}
            lastTurn={lastTurn}
            customers={customers}
            language={language}
            liveHighlightTxIds={liveHighlightTxIds}
            onRunPhrase={runTextTurn}
            onManualTx={handleManualTx}
            onSelectCustomerById={(id) => openCustomerLedgerById(id)}
          />
        )}

        {tab === "customers" && (
          <CustomersScreen
            customers={customers}
            language={language}
            highlightCustomerId={liveHighlightCustId}
            onSearch={(q) => refreshAll(q)}
            onCreateCustomer={handleCreateCustomer}
            onUpdateCustomer={handleUpdateCustomer}
            onDeleteCustomer={handleDeleteCustomer}
            onToggleOptIn={handleToggleOptIn}
            onSendReminder={(id) => api.sendReminder(id, language)}
            onOpenLedger={(c) => openCustomerLedgerById(c.id)}
            onOpenLedgersPage={() => navigateToTab("ledgers")}
          />
        )}

        {tab === "ledgers" && (
          <CustomerLedgersScreen
            customers={customers}
            allTransactions={allTransactions}
            language={language}
            highlightCustomerId={liveHighlightCustId}
            highlightTxIds={liveHighlightTxIds}
            focusCustomerId={focusLedgersCustId}
            onClearFocus={() => setFocusLedgersCustId(null)}
            onManualTx={handleManualTx}
            onCreateCustomer={handleCreateCustomer}
            onUpdateCustomer={handleUpdateCustomer}
            onDeleteCustomer={handleDeleteCustomer}
            onToggleOptIn={handleToggleOptIn}
            onSendReminder={(id) => api.sendReminder(id, language)}
          />
        )}

        {tab === "settings" && (
          <SettingsScreen
            currentUser={currentUser}
            onLogout={handleLogout}
            statusInfo={statusInfo}
            language={language}
            aiMode={aiMode}
            speechProvider={speechProvider}
            ephemeral={ephemeral}
            useDirectAudio={useDirectAudio}
            useLiveStream={useLiveStream && canLiveStream}
            liveWsSupported={liveWsSupported}
            liveEphemeralSupported={liveEphemeralSupported}
            canLiveStream={canLiveStream}
            interactiveConfirm={interactiveConfirm}
            onUpdateSetting={handleUpdateSettings}
            onToggleDirectAudio={setUseDirectAudio}
            onToggleLiveStream={(enabled) => {
              if (!enabled || !canLiveStream) {
                stopLiveSession();
                setUseLiveStream(false);
                return;
              }
              setUseLiveStream(true);
            }}
            onToggleInteractiveConfirm={(val) => {
              setInteractiveConfirm(val);
              if (liveConnected || liveConnecting) {
                stopLiveSession();
              }
            }}
            onWipeTranscripts={async () => {
              await api.wipeTranscripts();
              await refreshAll();
            }}
          />
        )}
      </main>

      <RightSlideDashboard
        open={drawerOpen}
        activeTab={tab}
        currentUser={currentUser}
        onSelectTab={navigateToTab}
        onLogout={handleLogout}
        onClose={() => setDrawerOpen(false)}
      />
    </div>
  );
}
