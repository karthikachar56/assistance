const OFFLINE_QUEUE_KEY = "voice_khata_offline_queue_v1";

async function request(path, options = {}) {
  const res = await fetch(path, options);
  if (!res.ok) {
    let detail = `HTTP ${res.status}`;
    try {
      const err = await res.json();
      if (err?.detail) detail = err.detail;
    } catch {
      // ignore non-json error body
    }
    throw new Error(detail);
  }
  const ct = res.headers.get("content-type") || "";
  if (ct.includes("application/json")) return res.json();
  return res.text();
}

export const api = {
  status: () => request("/api/status"),
  dashboard: () => request("/api/dashboard"),
  customers: (q = "") => request(`/api/customers${q ? `?q=${encodeURIComponent(q)}` : ""}`),
  createCustomer: (payload) =>
    request("/api/customers", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    }),
  updateCustomer: (id, payload) =>
    request(`/api/customers/${id}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    }),
  deleteCustomer: (id) => request(`/api/customers/${id}`, { method: "DELETE" }),
  customerTransactions: (id) => request(`/api/customers/${id}/transactions`),
  manualTransaction: (payload) =>
    request("/api/transactions", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    }),
  sendReminder: (customerId, language) =>
    request("/api/reminders/send", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ customer_id: customerId, language }),
    }),
  voiceTurnText: (payload) =>
    request("/api/voice/turn", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    }),
  voiceTurnAudio: ({
    blob,
    filename = "clip.wav",
    sessionId,
    language,
    aiMode,
    includeTts,
    fallbackTranscript = "",
    interactiveConfirm = false,
    pendingAction = null,
    lastCommittedTxIds = null,
  }) => {
    const fd = new FormData();
    fd.append("file", blob, filename);
    fd.append("session_id", sessionId);
    fd.append("language", language);
    fd.append("ai_mode", aiMode);
    fd.append("include_tts", String(Boolean(includeTts)));
    fd.append("fallback_transcript", fallbackTranscript || "");
    fd.append("interactive_confirm", String(Boolean(interactiveConfirm)));
    if (pendingAction) {
      fd.append("pending_action_json", JSON.stringify(pendingAction));
    }
    if (Array.isArray(lastCommittedTxIds) && lastCommittedTxIds.length > 0) {
      fd.append("last_committed_tx_ids_json", JSON.stringify(lastCommittedTxIds));
    }
    return request("/api/voice/audio", { method: "POST", body: fd });
  },
  voiceHistory: () => request("/api/voice/history"),
  voiceTts: (payload) =>
    request("/api/voice/tts", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    }),
  liveToken: (payload) =>
    request("/api/voice/live-token", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    }),
  liveTool: (payload) =>
    request("/api/voice/live-tool", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    }),
  auditLogs: () => request("/api/audit"),
  updateSettings: (payload) =>
    request("/api/settings", {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    }),
  wipeTranscripts: () => request("/api/privacy/transcripts", { method: "DELETE" }),
};

export function getOfflineQueue() {
  try {
    return JSON.parse(localStorage.getItem(OFFLINE_QUEUE_KEY) || "[]");
  } catch {
    return [];
  }
}

export function enqueueOfflineTurn(item) {
  const queue = getOfflineQueue();
  queue.push({ ...item, queued_at: new Date().toISOString() });
  localStorage.setItem(OFFLINE_QUEUE_KEY, JSON.stringify(queue));
  return queue;
}

export async function flushOfflineQueue() {
  const queue = getOfflineQueue();
  if (queue.length === 0) return { flushed: 0, remaining: 0 };
  const remaining = [];
  let flushed = 0;
  for (const item of queue) {
    try {
      await api.voiceTurnText(item);
      flushed += 1;
    } catch {
      remaining.push(item);
    }
  }
  localStorage.setItem(OFFLINE_QUEUE_KEY, JSON.stringify(remaining));
  return { flushed, remaining: remaining.length };
}

