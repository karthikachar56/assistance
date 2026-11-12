import React, { useEffect, useState } from "react";
import {
  Bell,
  BellOff,
  CheckCircle2,
  Download,
  LayoutGrid,
  Pencil,
  Plus,
  Trash2,
  X,
} from "lucide-react";
import { formatINR } from "../utils/numberNormalizer";
import { api } from "../api/client";
import { CustomerEditForm } from "./CustomerEditForm";

/**
 * Shared customer khata block displaying full customer metadata, actions,
 * credit/payment summary, and every transaction for that customer.
 */
export function CustomerLedgerCard({
  customer,
  transactions = [],
  highlightTxIds = [],
  isHighlighted = false,
  inline = false,
  onUpdateCustomer,
  onDeleteCustomer,
  onAddTransaction,
  onToggleOptIn,
  onSendReminder,
  onOpenInLedgers,
  onClose,
}) {
  const [editing, setEditing] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [addingTx, setAddingTx] = useState(false);
  const [amount, setAmount] = useState("");
  const [txType, setTxType] = useState("credit");
  const [note, setNote] = useState("");
  const [savingTx, setSavingTx] = useState(false);

  useEffect(() => {
    setEditing(false);
    setConfirmDelete(false);
    setAddingTx(false);
    setAmount("");
    setNote("");
  }, [customer?.id]);

  if (!customer) return null;

  const hiSet = new Set(highlightTxIds);
  const hasFreshTx = transactions.some((tx) => hiSet.has(tx.id));
  const outstanding = Number(customer.outstanding_balance || 0);
  const statusTag =
    outstanding > 0 ? "Balance Due" : outstanding < 0 ? "Advance Paid" : "Settled";

  async function handleSaveTx(e) {
    e.preventDefault();
    const numericAmount = Number(amount);
    if (!onAddTransaction || !numericAmount || numericAmount <= 0 || savingTx) return;
    setSavingTx(true);
    try {
      await onAddTransaction({
        customer_id: customer.id,
        amount: numericAmount,
        tx_type: txType,
        note: note.trim() || null,
      });
      setAmount("");
      setNote("");
      setAddingTx(false);
    } finally {
      setSavingTx(false);
    }
  }

  return (
    <>
      <div className="customer-block-head">
        <div className="customer-block-title">
          <div className="customer-block-eyebrow">
            <span>Customer Digital Khata {inline ? "· Live View" : ""}</span>
            {customer.is_synthetic && (
              <span className="pill" style={{ fontSize: "0.66rem", padding: "1px 7px" }}>
                Demo
              </span>
            )}
            {(isHighlighted || hasFreshTx) && <span className="live-tag">LIVE</span>}
          </div>
          <h3>{customer.name}</h3>
          <div className="customer-block-meta">
            {customer.phone ? `Phone: ${customer.phone} · ` : ""}
            Lang: {customer.preferred_language || "hi-IN"} ·{" "}
            {customer.reminder_opt_in ? "Reminders ON" : "Reminders OFF"}
          </div>
        </div>

        <div className="customer-block-actions">
          {onAddTransaction && (
            <button
              className="btn btn-secondary btn-sm"
              type="button"
              onClick={() => {
                setEditing(false);
                setConfirmDelete(false);
                setAddingTx((v) => !v);
              }}
              title="Add credit or payment entry for this customer"
            >
              <Plus size={14} /> Entry
            </button>
          )}
          {onToggleOptIn && (
            <button
              className="btn btn-secondary btn-sm"
              type="button"
              onClick={() => onToggleOptIn(customer)}
              title={
                customer.reminder_opt_in
                  ? "Opted in for reminders (click to opt out)"
                  : "Not opted in (click to opt in)"
              }
            >
              {customer.reminder_opt_in ? (
                <Bell size={14} color="#0d9488" />
              ) : (
                <BellOff size={14} color="#9ca3af" />
              )}
            </button>
          )}
          {onSendReminder && (
            <button
              className="btn btn-secondary btn-sm"
              type="button"
              disabled={!customer.reminder_opt_in || outstanding <= 0}
              onClick={() => onSendReminder(customer)}
              title="Send polite opt-in reminder"
            >
              Remind
            </button>
          )}
          {onOpenInLedgers && (
            <button
              className="btn btn-secondary btn-sm"
              type="button"
              onClick={() => onOpenInLedgers(customer)}
              title="Open in All Customer Ledgers page"
            >
              <LayoutGrid size={14} /> All Ledgers
            </button>
          )}
          {onUpdateCustomer && (
            <button
              className="btn btn-secondary btn-sm"
              type="button"
              onClick={() => {
                setConfirmDelete(false);
                setAddingTx(false);
                setEditing((v) => !v);
              }}
              title="Edit customer details"
            >
              <Pencil size={14} /> Edit
            </button>
          )}
          {onDeleteCustomer && !confirmDelete && (
            <button
              className="btn btn-danger btn-sm"
              type="button"
              onClick={() => {
                setEditing(false);
                setAddingTx(false);
                setConfirmDelete(true);
              }}
              title="Delete customer manually"
            >
              <Trash2 size={14} /> Delete
            </button>
          )}
          {onDeleteCustomer && confirmDelete && (
            <>
              <button
                className="btn btn-danger btn-sm"
                type="button"
                onClick={async () => {
                  await onDeleteCustomer(customer.id);
                  setConfirmDelete(false);
                }}
              >
                Confirm Delete
              </button>
              <button
                className="btn btn-secondary btn-sm"
                type="button"
                onClick={() => setConfirmDelete(false)}
              >
                Cancel
              </button>
            </>
          )}
          {onClose && (
            <button
              className="btn btn-secondary btn-sm"
              type="button"
              onClick={onClose}
              title="Close customer details"
            >
              <X size={16} />
            </button>
          )}
        </div>
      </div>

      {editing && onUpdateCustomer && (
        <div style={{ marginBottom: 14 }}>
          <CustomerEditForm
            customer={customer}
            onSave={async (payload) => {
              await onUpdateCustomer(customer.id, payload);
              setEditing(false);
            }}
            onCancel={() => setEditing(false)}
          />
        </div>
      )}

      {addingTx && onAddTransaction && (
        <form onSubmit={handleSaveTx} className="customer-inline-tx-form">
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
          <select
            className="text-input"
            value={txType}
            onChange={(e) => setTxType(e.target.value)}
          >
            <option value="credit">Credit (Udhaar)</option>
            <option value="payment">Payment</option>
          </select>
          <input
            className="text-input"
            placeholder="Note (optional)"
            value={note}
            onChange={(e) => setNote(e.target.value)}
          />
          <div style={{ display: "flex", gap: 6 }}>
            <button className="btn btn-primary btn-sm" type="submit" disabled={savingTx}>
              <CheckCircle2 size={14} /> {savingTx ? "Saving..." : "Save"}
            </button>
            <button
              className="btn btn-secondary btn-sm"
              type="button"
              onClick={() => setAddingTx(false)}
              disabled={savingTx}
            >
              Cancel
            </button>
          </div>
        </form>
      )}

      <div className="customer-balance-box">
        <div>
          <div className="customer-balance-head">
            <span className="stat-label">Outstanding Balance (Credit − Payment)</span>
            <span className={`badge ${outstanding > 0 ? "credit" : "payment"}`}>{statusTag}</span>
          </div>
          <div className={`stat-val ${outstanding > 0 ? "credit" : "payment"}`}>
            {formatINR(outstanding)}
          </div>
          <div className="customer-balance-sub">
            Total Credit: {formatINR(customer.total_credit)} · Total Paid:{" "}
            {formatINR(customer.total_payment)} · {transactions.length}{" "}
            {transactions.length === 1 ? "transaction" : "transactions"}
          </div>
        </div>
        <button
          className="btn btn-secondary btn-sm"
          type="button"
          onClick={() => api.exportCsv(customer.id)}
        >
          <Download size={14} /> CSV
        </button>
      </div>

      <div className="customer-block-tx-header">
        <span>All Transactions ({transactions.length})</span>
      </div>

      <div className="tx-list">
        {transactions.length === 0 ? (
          <div style={{ color: "var(--muted)", fontSize: "0.86rem", padding: "6px 0" }}>
            No transactions recorded yet.
          </div>
        ) : (
          transactions.map((tx) => {
            const isFresh = hiSet.has(tx.id);
            return (
              <div key={tx.id} className={`tx-row ${isFresh ? "live-flash" : ""}`}>
                <div>
                  <div style={{ fontWeight: 600 }}>
                    {tx.transaction_date} ·{" "}
                    <span className={`badge ${tx.tx_type}`}>{tx.tx_type}</span>
                    {isFresh && <span className="live-tag">LIVE</span>}
                  </div>
                  <div style={{ fontSize: "0.76rem", color: "var(--muted)" }}>
                    Receipt #{tx.id}
                    {tx.note ? ` · ${tx.note}` : ""}
                  </div>
                </div>
                <div className={`tx-amt ${tx.tx_type}`}>
                  {tx.tx_type === "credit" ? "+" : "-"}
                  {formatINR(tx.amount)}
                </div>
              </div>
            );
          })
        )}
      </div>
    </>
  );
}

