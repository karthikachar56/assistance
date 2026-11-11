import React, { useEffect, useRef, useState } from "react";
import { Mic, MicOff, PanelRightOpen, Radio, Send } from "lucide-react";
import { api, enqueueOfflineTurn, flushOfflineQueue, getOfflineQueue } from "./api/client";
import { GeminiLiveClient } from "./audio/liveClient";
import { ShopAudioRecorder, playReplyAudio } from "./audio/recorder";
import { ConsentBanner } from "./components/ConsentBanner";
import { CustomerLedgerModal } from "./components/CustomerLedgerModal";
import { CustomersScreen } from "./components/CustomersScreen";
import { DashboardScreen } from "./components/DashboardScreen";
import { JarvisHud } from "./components/JarvisHud";
import { NAV_SECTIONS, RightSlideDashboard } from "./components/RightSlideDashboard";
import { SettingsScreen } from "./components/SettingsScreen";
import { previewSpokenAmount } from "./utils/numberNormalizer";
import { inferNavigationFromText } from "./utils/voiceNavigator";

export default function App() {
  const [sessionId] = useState(() => `khata-${Math.random().toString(36).slice(2, 8)}`);
  const [tab, setTab] = useState("home");
  const [drawerOpen, setDrawerOpen] = useState(false);

  useEffect(() => {
    setDrawerOpen(false);
  }, [tab]);
  const [statusInfo, setStatusInfo] = useState(null);
  const [dashboard, setDashboard] = useState(null);
  const [customers, setCustomers] = useState([]);
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
  const liveAiAccumRef = useRef("");
  const liveUserAccumRef = useRef("");
  const initializedAudioPrefRef = useRef(false);
  const customersRef = useRef([]);
  const selectedCustomerIdRef = useRef(null);
  const tabRef = useRef("home");
  const turnNavigatedRef = useRef(false);
  const highlightTimerRef = useRef(null);

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
      const [st, dash, custs] = await Promise.all([
        api.status(),
        api.dashboard(),
        api.customers(searchQuery),
      ]);
      setStatusInfo(st);
      setDashboard(dash);
      setCustomers(custs);
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
      if (s.preferred_language) setLanguage(s.preferred_language);
      if (s.ai_mode) setAiMode(s.ai_mode);
      if (s.speech_provider) setSpeechProvider(s.speech_provider);
      setEphemeral(s.ephemeral_transcripts === "true");
      setConsentAccepted(s.dpdp_consent_accepted === "true");
      if (!initializedAudioPrefRef.current) {
        initializedAudioPrefRef.current = true;
        if (st.gemini_configured || st.gnani_configured) {
          setUseDirectAudio(true);
        }
        if (st.gemini_configured) {
          setUseLiveStream(true);
        }
      }
      setErrorMsg("");
      return { custs };
    } catch (err) {
      setErrorMsg(`Backend unreachable (${err.message}). Start FastAPI on port 8000.`);
      return null;
    }
  }

  useEffect(() => {
    refreshAll();
    const onOnline = async () => {
      setOnline(true);
      const res = await flushOfflineQueue();
      setOfflineCount(res.remaining);
      if (res.flushed > 0) refreshAll();
    };
    const onOffline = () => setOnline(false);
    window.addEventListener("online", onOnline);
    window.addEventListener("offline", onOffline);
    return () => {
      window.removeEventListener("online", onOnline);
      window.removeEventListener("offline", onOffline);
      liveClientRef.current?.stop();
    };
  }, []);

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
    if (useLiveStream && liveConnected && liveClientRef.current?.sendText(clean)) {
      appendConversation("user", clean);
      setTextDraft("");
      return;
    }
    setIsProcessing(true);
    setErrorMsg("");
    const payload = {
      session_id: sessionId,
      transcript: clean,
      language,
      ai_mode: aiMode,
      include_tts: Boolean(statusInfo?.gemini_configured || statusInfo?.gnani_configured),
      interactive_confirm: interactiveConfirm,
    };
    try {
      const res = await api.voiceTurnText(payload);
      setLastTurn(res);
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
        const q = enqueueOfflineTurn(payload);
        setOfflineCount(q.length);
        setErrorMsg("Offline: voice transaction queued locally for auto-sync.");
      } else {
        setErrorMsg(err.message);
      }
    } finally {
      setIsProcessing(false);
    }
  }

  async function toggleMic() {
    if (useLiveStream) {
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

        const client = new GeminiLiveClient();
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
            setLastTurn(payload);
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
        const rec = new ShopAudioRecorder();
        recorderRef.current = rec;
        setLiveUserText("");
        await rec.start({
          language,
          onLevel: (lvl) => setMicLevel(lvl),
          onInterimTranscript: (t) => {
            setTextDraft(t);
            setLiveUserText(t);
          },
        });
        setIsRecording(true);
        setErrorMsg("");
      } catch (err) {
        setErrorMsg(`Microphone error: ${err.message}`);
      }
    } else {
      setIsRecording(false);
      setMicLevel(0);
      const result = await recorderRef.current.stop();
      const canUploadAudio = useDirectAudio && (statusInfo?.gemini_configured || statusInfo?.gnani_configured);
      if (canUploadAudio) {
        setIsProcessing(true);
        try {
          const res = await api.voiceTurnAudio({
            blob: result.wavBlob,
            sessionId,
            language,
            aiMode,
            includeTts: true,
            fallbackTranscript: result.browserTranscript,
            interactiveConfirm,
          });
          setLastTurn(res);
          setLiveUserText(res.transcript);
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
        setErrorMsg(
          "No speech transcript detected. Check mic permission, type below, or enable Live Voice Mode."
        );
      }
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

  async function handleUpdateCustomer(id, payload) {
    await api.updateCustomer(id, payload);
    setLiveHighlightCustId(id);
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

  return (
    <div className="split-shell">
      <aside className="ai-pane" aria-label="Voice Khata AI Assistant">
        <JarvisHud
          liveConnected={liveConnected}
          liveConnecting={liveConnecting}
          aiSpeaking={aiSpeaking}
          isRecording={isRecording}
          isProcessing={isProcessing}
          useLiveStream={useLiveStream}
          interactiveConfirm={interactiveConfirm}
          geminiConfigured={Boolean(statusInfo?.gemini_configured)}
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
            if (!enabled) {
              stopLiveSession();
            }
            setUseLiveStream(enabled);
          }}
          onToggleInteractiveConfirm={(val) => {
            setInteractiveConfirm(val);
            if (liveConnected || liveConnecting) {
              stopLiveSession();
            }
          }}
          onQuickReply={runTextTurn}
        />

        <div className="ai-dock-card">
          <div className="voice-dock-inner">
            <button
              className={`mic-btn ${isRecording || liveConnected ? "recording" : ""}`}
              onClick={toggleMic}
              disabled={isProcessing}
              title={useLiveStream ? "Toggle Gemini 3.8 Live Stream" : "Tap to speak / tap again to stop"}
            >
              {useLiveStream ? <Radio size={22} /> : isRecording ? <MicOff size={22} /> : <Mic size={22} />}
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
              <h1>Voice Khata</h1>
              <small>Kirana AI Digital Accountant · Gemini 3.8 / 4 Argon</small>
            </div>
          </div>
          <div className="topbar-right">
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
            onManualTx={async (payload) => {
              await api.manualTransaction(payload);
              await refreshAll();
            }}
            onSelectCustomerById={(id) => openCustomerLedgerById(id)}
          />
        )}

        {tab === "customers" && (
          <CustomersScreen
            customers={customers}
            language={language}
            highlightCustomerId={liveHighlightCustId}
            onSearch={(q) => refreshAll(q)}
            onCreateCustomer={async (payload) => {
              await api.createCustomer(payload);
              await refreshAll();
            }}
            onUpdateCustomer={handleUpdateCustomer}
            onDeleteCustomer={handleDeleteCustomer}
            onToggleOptIn={async (c) => {
              await api.updateCustomer(c.id, { reminder_opt_in: !c.reminder_opt_in });
              await refreshAll();
            }}
            onSendReminder={(id) => api.sendReminder(id, language)}
            onOpenLedger={(c) => openCustomerLedgerById(c.id)}
          />
        )}

        {tab === "settings" && (
          <SettingsScreen
            statusInfo={statusInfo}
            language={language}
            aiMode={aiMode}
            speechProvider={speechProvider}
            ephemeral={ephemeral}
            useDirectAudio={useDirectAudio}
            useLiveStream={useLiveStream}
            interactiveConfirm={interactiveConfirm}
            onUpdateSetting={handleUpdateSettings}
            onToggleDirectAudio={setUseDirectAudio}
            onToggleLiveStream={(enabled) => {
              if (!enabled) {
                stopLiveSession();
              }
              setUseLiveStream(enabled);
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
        onSelectTab={(nextTab) => {
          setTab(nextTab);
          setDrawerOpen(false);
        }}
        onClose={() => setDrawerOpen(false)}
      />
    </div>
  );
}
