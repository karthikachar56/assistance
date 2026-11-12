import React, { useEffect } from "react";
import { CustomerLedgerCard } from "./CustomerLedgerCard";

export function CustomerLedgerModal({
  customer,
  transactions,
  highlightTxIds = [],
  inline = false,
  onUpdateCustomer,
  onDeleteCustomer,
  onOpenInLedgers,
  onClose,
}) {
  useEffect(() => {
    if (!customer || inline) return undefined;
    const onKeyDown = (e) => {
      if (e.key === "Escape") onClose?.();
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [customer, inline, onClose]);

  if (!customer) return null;

  const content = (
    <CustomerLedgerCard
      customer={customer}
      transactions={transactions}
      highlightTxIds={highlightTxIds}
      inline={inline}
      onUpdateCustomer={onUpdateCustomer}
      onDeleteCustomer={onDeleteCustomer}
      onOpenInLedgers={onOpenInLedgers}
      onClose={onClose}
    />
  );

  if (inline) {
    return <div className="card inline-ledger-panel">{content}</div>;
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
        {content}
      </div>
    </div>
  );
}
