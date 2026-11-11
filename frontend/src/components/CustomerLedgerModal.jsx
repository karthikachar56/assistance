import React, { useEffect, useState } from "react";
import { Download, Pencil, Trash2, X } from "lucide-react";
import { formatINR } from "../utils/numberNormalizer";
import { CustomerEditForm } from "./CustomerEditForm";

export function CustomerLedgerModal({
  customer,
  transactions,
  highlightTxIds = [],
  inline = false,
  onUpdateCustomer,
  onDeleteCustomer,
  onClose,
}) {
  const [editing, setEditing] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);

  useEffect(() => {
    setEditing(false);
    setConfirmDelete(false);
  }, [customer?.id]);

  useEffect(() => {
    if (!customer || inline) return undefined;
    const onKeyDown = (e) => {
      if (e.key === "Escape") onClose?.();
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [customer, inline, onClose]);

  if (!customer) return null;
  const hiSet = new Set(highlightTxIds);

  const body = (
    <>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: 10, marginBottom: 12, flexWrap: "wrap" }}>
        <div>
          <div style={{ fontSize: "0.72rem", fontWeight: 700, color: "var(--muted)", textTransform: "uppercase" }}>
            Customer Digital Khata {inline ? "· Live View" : ""}
          </div>
          <h2 style={{ margin: "2px 0 0", fontSize: "1.25rem" }}>{customer.name}</h2>
          <div style={{ fontSize: "0.78rem", color: "var(--muted)", marginTop: 2 }}>
            {customer.phone ? `Phone: ${customer.phone} · ` : ""}
            Lang: {customer.preferred_language || "hi-IN"} ·{" "}
            {customer.reminder_opt_in ? "Reminders ON" : "Reminders OFF"}
          </div>
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: 6, flexWrap: "wrap" }}>
          {onUpdateCustomer && (
            <button
              className="btn btn-secondary"
              type="button"
              onClick={() => {
                setConfirmDelete(false);
                setEditing((v) => !v);
              }}
              title="Edit customer details"
            >
              <Pencil size={14} /> Edit
            </button>
          )}
          {onDeleteCustomer && !confirmDelete && (
            <button
              className="btn btn-danger"
              type="button"
              onClick={() => {
                setEditing(false);
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
                className="btn btn-danger"
                type="button"
                onClick={async () => {
                  await onDeleteCustomer(customer.id);
                  setConfirmDelete(false);
                }}
              >
                Confirm Delete
              </button>
              <button className="btn btn-secondary" type="button" onClick={() => setConfirmDelete(false)}>
                Cancel
              </button>
            </>
          )}
          <button className="btn btn-secondary" onClick={onClose} title="Close customer details">
            <X size={16} />
          </button>
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

      <div className="card" style={{ background: "var(--bg)", marginBottom: 14, boxShadow: "none" }}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 12, flexWrap: "wrap" }}>
          <div>
            <div className="stat-label">Outstanding Balance (Credit − Payment)</div>
            <div className={`stat-val ${customer.outstanding_balance > 0 ? "credit" : "payment"}`}>
              {formatINR(customer.outstanding_balance)}
            </div>
            <div style={{ fontSize: "0.78rem", color: "var(--muted)", marginTop: 4 }}>
              Total Credit: {formatINR(customer.total_credit)} · Total Paid: {formatINR(customer.total_payment)}
            </div>
          </div>
          <a
            className="btn btn-secondary"
            href={`/api/export/csv?customer_id=${customer.id}`}
            style={{ textDecoration: "none" }}
          >
            <Download size={15} /> CSV
          </a>
        </div>
      </div>

      <div className="tx-list">
        {transactions.length === 0 ? (
          <div style={{ color: "var(--muted)", fontSize: "0.88rem" }}>No transactions recorded yet.</div>
        ) : (
          transactions.map((tx) => {
            const isFresh = hiSet.has(tx.id);
            return (
              <div key={tx.id} className={`tx-row ${isFresh ? "live-flash" : ""}`}>
                <div>
                  <div style={{ fontWeight: 600 }}>
                    {tx.transaction_date} · <span className={`badge ${tx.tx_type}`}>{tx.tx_type}</span>
                    {isFresh && <span className="live-tag">LIVE</span>}
                  </div>
                  <div style={{ fontSize: "0.76rem", color: "var(--muted)" }}>
                    Receipt #{tx.id} {tx.note ? `· ${tx.note}` : ""}
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

  if (inline) {
    return <div className="card inline-ledger-panel">{body}</div>;
  }

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div
        className="modal-card"
        role="dialog"
        aria-modal="true"
        aria-label={`${customer.name} details`}
        onClick={(e) => e.stopPropagation()}
      >
        {body}
      </div>
    </div>
  );
}
