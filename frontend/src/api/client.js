const OFFLINE_QUEUE_KEY = "voice_khata_offline_queue_v1";
const AUTH_TOKEN_KEY = "voice_khata_auth_token_v1";

export function getAuthToken() {
  try {
    return localStorage.getItem(AUTH_TOKEN_KEY) || "";
  } catch {
    return "";
  }
}

export function setAuthToken(token) {
  try {
    if (token) localStorage.setItem(AUTH_TOKEN_KEY, token);
    else localStorage.removeItem(AUTH_TOKEN_KEY);
  } catch {
    // ignore storage quota errors
  }
}

export function clearAuthToken() {
  setAuthToken("");
}

async function request(path, options = {}) {
  const token = getAuthToken();
  const headers = new Headers(options.headers || {});
  if (token && !headers.has("Authorization")) {
    headers.set("Authorization", `Bearer ${token}`);
  }
  const res = await fetch(path, { ...options, headers });
  if (!res.ok) {
    let detail = `HTTP ${res.status}`;
    try {
      const err = await res.json();
      if (typeof err?.detail === "string") {
        detail = err.detail;
      } else if (Array.isArray(err?.detail) && err.detail[0]?.msg) {
        detail = err.detail[0].msg;
      }
    } catch {
      // ignore non-json error body
    }
    const error = new Error(detail);
    error.status = res.status;
    throw error;
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
  transactions: (customerId = null) =>
    request(customerId != null ? `/api/transactions?customer_id=${customerId}` : "/api/transactions"),
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
  authSignup: (payload) =>
    request("/api/auth/signup", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    }),
  authLogin: (payload) =>
    request("/api/auth/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    }),
  authMe: () => request("/api/auth/me"),
  authLogout: () => request("/api/auth/logout", { method: "POST" }),
  exportCsv: async (customerId = null) => {
    const token = getAuthToken();
    const headers = new Headers();
    if (token) headers.set("Authorization", `Bearer ${token}`);
    const url =
      customerId != null
        ? `/api/export/csv?customer_id=${encodeURIComponent(customerId)}`
        : "/api/export/csv";
    const res = await fetch(url, { headers });
    if (!res.ok) {
      throw new Error(`CSV export failed (HTTP ${res.status})`);
    }
    const blob = await res.blob();
    const objectUrl = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = objectUrl;
    link.download = `voice_khata_${customerId ?? "all"}.csv`;
    document.body.appendChild(link);
    link.click();
    link.remove();
    URL.revokeObjectURL(objectUrl);
  },
};

function offlineQueueKey(userId = null) {
  return userId != null ? `${OFFLINE_QUEUE_KEY}_u${userId}` : OFFLINE_QUEUE_KEY;
}

export function getOfflineQueue(userId = null) {
  try {
    return JSON.parse(localStorage.getItem(offlineQueueKey(userId)) || "[]");
  } catch {
    return [];
  }
}

export function enqueueOfflineTurn(item, userId = null) {
  const queue = getOfflineQueue(userId);
  queue.push({ ...item, queued_at: new Date().toISOString() });
  localStorage.setItem(offlineQueueKey(userId), JSON.stringify(queue));
  return queue;
}

export async function flushOfflineQueue(userId = null) {
  const queue = getOfflineQueue(userId);
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
  localStorage.setItem(offlineQueueKey(userId), JSON.stringify(remaining));
  return { flushed, remaining: remaining.length };
}

