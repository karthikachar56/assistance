import React, { useState } from "react";
import { ArrowDownLeft, ArrowUpRight, CheckCircle2, Clock, Plus, Sparkles, Volume2 } from "lucide-react";
import { formatINR } from "../utils/numberNormalizer";
import { playReplyAudio } from "../audio/recorder";

const DEMO_PHRASES = [
  "Ramesh ka 340 udhaar, kal 200 diya tha",
  "Suresh ka teen sau chaalis udhaar",
  "Nahi, 430",
  "Ramesh ka 300 udhaar",
  "Ramesh Gowda",
  "Aaj kitna udhaar gaya?",
  "Suresh ka total batao",
  "Sabse zyada udhaar kiske paas hai?",
];

function BreakdownChips({ items = [], tone = "credit", emptyText = "", onSelectCustomerById }) {
  if (!items || items.length === 0) {
    return emptyText ? <div className="stat-breakdown-empty">{emptyText}</div> : null;
  }
  return (
    <div className="stat-breakdown">
      {items.map((item) => (
        <button
          key={item.customer_id}
          type="button"
          className={`mini-cust-pill ${tone}`}
          onClick={() => onSelectCustomerById?.(item.customer_id)}
          title={`Open ${item.customer_name}'s khata`}
        >
          <span>{item.customer_name}</span>
          <strong>{formatINR(item.amount)}</strong>
        </button>
      ))}
    </div>
  );
}

export function DashboardScreen({
  dashboard,
  lastTurn,
  customers,
  language,
  liveHighlightTxIds = [],
  onRunPhrase,
  onManualTx,
  onSelectCustomerById,
}) {
  const [showManual, setShowManual] = useState(false);
  const [custId, setCustId] = useState("");
  const [amount, setAmount] = useState("");
  const [txType, setTxType] = useState("credit");
  const [note, setNote] = useState("");

  async function submitManual(e) {
    e.preventDefault();
    if (!custId || !amount) return;
    await onManualTx({
      customer_id: Number(custId),
      amount: Number(amount),
      tx_type: txType,
      note: note.trim() || null,
    });
    setAmount("");
    setNote("");
    setShowManual(false);
  }

  return (
    <div>
      {lastTurn && (
        <div className="reply-banner">
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: 10 }}>
            <div>
              <div style={{ fontSize: "0.75rem", fontWeight: 700, color: "var(--accent)", textTransform: "uppercase" }}>
                Voice Khata AI · {lastTurn.status.replace("_", " ")}
              </div>
              <div style={{ fontSize: "0.84rem", color: "var(--muted)", marginTop: 2 }}>
                You: &ldquo;{lastTurn.transcript}&rdquo;
              </div>
              <div style={{ fontSize: "1.02rem", fontWeight: 700, marginTop: 6 }}>
                {lastTurn.spoken_reply}
              </div>
            </div>
            <button
              className="btn btn-secondary"
              onClick={() =>
                playReplyAudio({
                  audioBase64: lastTurn.audio_base64,
                  audioMimeType: lastTurn.audio_mime_type,
                  text: lastTurn.spoken_reply,
                  language,
                })
              }
              title="Replay spoken answer"
            >
              <Volume2 size={16} />
            </button>
          </div>

          {lastTurn.customer_breakdown?.length > 0 && (
            <BreakdownChips
              items={lastTurn.customer_breakdown}
              tone={lastTurn.transactions?.[0]?.tx_type === "payment" ? "payment" : "credit"}
              onSelectCustomerById={onSelectCustomerById}
            />
          )}

          {lastTurn.candidate_customers?.length > 0 && (
            <div className="demo-chips">
              {lastTurn.candidate_customers.map((name) => (
                <button key={name} className="chip-btn" onClick={() => onRunPhrase(name)}>
                  Select {name}
                </button>
              ))}
            </div>
          )}

          {lastTurn.latency && (
            <div className="latency-bar">
              <span>
                <Sparkles size={12} style={{ verticalAlign: "-1px" }} /> {lastTurn.model_used}
              </span>
              <span>FastPath: {lastTurn.latency.fast_path_ms}ms</span>
              <span>AI: {lastTurn.latency.ai_ms}ms</span>
              <span>DB: {lastTurn.latency.ledger_ms}ms</span>
              <span>TTS: {lastTurn.latency.tts_ms}ms</span>
              <span
                style={{
                  fontWeight: 700,
                  color: lastTurn.latency.total_ms < 2000 ? "#065f46" : "#b91c1c",
                }}
              >
                <Clock size={12} style={{ verticalAlign: "-1px" }} /> Total: {lastTurn.latency.total_ms}ms{" "}
                {lastTurn.latency.total_ms < 2000 ? "(<2s SLA)" : ""}
              </span>
            </div>
          )}
        </div>
      )}

      <div className="stats-grid">
        <div className="card">
          <div className="stat-label">Credit Today (Udhaar)</div>
          <div className="stat-val credit">{formatINR(dashboard?.today_credit)}</div>
          <BreakdownChips
            items={dashboard?.today_credit_customers}
            tone="credit"
            emptyText="No credit today"
            onSelectCustomerById={onSelectCustomerById}
          />
        </div>
        <div className="card">
          <div className="stat-label">Payments Today</div>
          <div className="stat-val payment">{formatINR(dashboard?.today_payment)}</div>
          <BreakdownChips
            items={dashboard?.today_payment_customers}
            tone="payment"
            emptyText="No payments today"
            onSelectCustomerById={onSelectCustomerById}
          />
        </div>
        <div className="card">
          <div className="stat-label">Total Outstanding</div>
          <div className="stat-val accent">{formatINR(dashboard?.total_outstanding)}</div>
          <BreakdownChips
            items={dashboard?.outstanding_customers}
            tone="accent"
            emptyText="All settled"
            onSelectCustomerById={onSelectCustomerById}
          />
        </div>
        <div className="card">
          <div className="stat-label">Total Customers</div>
          <div className="stat-val">{dashboard?.total_customers ?? 0}</div>
        </div>
      </div>

      <div className="card" style={{ marginBottom: 16 }}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
          <div>
            <div style={{ fontWeight: 700 }}>Quick Voice / Judge Test Prompts</div>
            <div style={{ fontSize: "0.8rem", color: "var(--muted)" }}>
              Tap any prompt or hold the mic button below to speak live in Hindi, Kannada, Tamil, Telugu, or English.
            </div>
          </div>
          <button className="btn btn-secondary" onClick={() => setShowManual((v) => !v)}>
            <Plus size={15} /> Manual Entry
          </button>
        </div>

        <div className="demo-chips">
          {DEMO_PHRASES.map((phrase) => (
            <button key={phrase} className="chip-btn" onClick={() => onRunPhrase(phrase)}>
              {phrase}
            </button>
          ))}
        </div>

        {showManual && (
          <form onSubmit={submitManual} style={{ marginTop: 14, display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(140px, 1fr))", gap: 8 }}>
            <select className="text-input" value={custId} onChange={(e) => setCustId(e.target.value)} required>
              <option value="">Select Customer...</option>
              {customers.map((c) => (
                <option key={c.id} value={c.id}>
                  {c.name}
                </option>
              ))}
            </select>
            <input
              className="text-input"
              type="number"
              min="1"
              step="0.01"
              placeholder="Amount ₹"
              value={amount}
              onChange={(e) => setAmount(e.target.value)}
              required
            />
            <select className="text-input" value={txType} onChange={(e) => setTxType(e.target.value)}>
              <option value="credit">Credit (Udhaar)</option>
              <option value="payment">Payment</option>
            </select>
            <input
              className="text-input"
              placeholder="Note (optional)"
              value={note}
              onChange={(e) => setNote(e.target.value)}
            />
            <button className="btn btn-primary" type="submit">
              <CheckCircle2 size={15} /> Save
            </button>
          </form>
        )}
      </div>

      <div className="card">
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 12 }}>
          <span style={{ fontWeight: 700 }}>Recent Transactions</span>
          <a className="btn btn-secondary" href="/api/export/csv" style={{ textDecoration: "none" }}>
            Export CSV
          </a>
        </div>
        <div className="tx-list">
          {(dashboard?.recent_transactions || []).map((tx) => {
            const isFresh = liveHighlightTxIds.includes(tx.id);
            return (
            <div
              key={tx.id}
              className={`tx-row ${isFresh ? "live-flash" : ""}`}
              style={{ cursor: "pointer" }}
              onClick={() => onSelectCustomerById(tx.customer_id)}
            >
              <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
                {tx.tx_type === "credit" ? (
                  <ArrowUpRight size={18} color="var(--credit)" />
                ) : (
                  <ArrowDownLeft size={18} color="var(--payment)" />
                )}
                <div>
                  <div style={{ fontWeight: 700 }}>
                    {tx.customer_name}
                    {isFresh && <span className="live-tag">LIVE</span>}
                  </div>
                  <div style={{ fontSize: "0.78rem", color: "var(--muted)" }}>
                    {tx.transaction_date} · <span className={`badge ${tx.tx_type}`}>{tx.tx_type}</span>
                    {tx.note ? ` · ${tx.note}` : ""}
                  </div>
                </div>
              </div>
              <div className={`tx-amt ${tx.tx_type}`}>
                {tx.tx_type === "credit" ? "+" : "-"}
                {formatINR(tx.amount)}
              </div>
            </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}

