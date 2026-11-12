import React, { useEffect, useMemo, useRef, useState } from "react";
import { Download, Search, UserPlus } from "lucide-react";
import {
  countCustomerBalanceGroups,
  filterAndSortCustomerLedgers,
  groupTransactionsByCustomerId,
  summarizeCustomerLedgers,
} from "../utils/customerLedgers";
import { formatINR } from "../utils/numberNormalizer";
import { CustomerLedgerCard } from "./CustomerLedgerCard";

/**
 * Separate workspace page displaying individual ledger blocks for every
 * customer with their balance summary and complete transaction list.
 */
export function CustomerLedgersScreen({
  customers = [],
  allTransactions = [],
  language,
  highlightCustomerId = null,
  highlightTxIds = [],
  focusCustomerId = null,
  onClearFocus,
  onManualTx,
  onCreateCustomer,
  onUpdateCustomer,
  onDeleteCustomer,
  onToggleOptIn,
  onSendReminder,
}) {
  const [search, setSearch] = useState("");
  const [balanceFilter, setBalanceFilter] = useState("all");
  const [sortBy, setSortBy] = useState("name");
  const [showAdd, setShowAdd] = useState(false);
  const [newName, setNewName] = useState("");
  const [newPhone, setNewPhone] = useState("");
  const [newOptIn, setNewOptIn] = useState(false);
  const [reminderPreview, setReminderPreview] = useState(null);
  const blockRefs = useRef(new Map());

  const txsByCustomer = useMemo(
    () => groupTransactionsByCustomerId(allTransactions),
    [allTransactions]
  );

  useEffect(() => {
    if (focusCustomerId == null) return;
    setSearch("");
    setBalanceFilter("all");
    const timer = setTimeout(() => {
      const node = blockRefs.current.get(focusCustomerId);
      if (node && typeof node.scrollIntoView === "function") {
        node.scrollIntoView({ behavior: "smooth", block: "start" });
      }
      onClearFocus?.();
    }, 60);
    return () => clearTimeout(timer);
  }, [focusCustomerId, customers.length, onClearFocus]);

  const counts = useMemo(() => countCustomerBalanceGroups(customers), [customers]);

  const filteredCustomers = useMemo(
    () => filterAndSortCustomerLedgers(customers, txsByCustomer, { search, balanceFilter, sortBy }),
    [customers, txsByCustomer, search, balanceFilter, sortBy]
  );

  const summary = useMemo(
    () => summarizeCustomerLedgers(filteredCustomers, txsByCustomer),
    [filteredCustomers, txsByCustomer]
  );

  async function handleAddCustomer(e) {
    e.preventDefault();
    if (!newName.trim() || !onCreateCustomer) return;
    await onCreateCustomer({
      name: newName.trim(),
      phone: newPhone.trim() || null,
      reminder_opt_in: newOptIn,
      preferred_language: language,
    });
    setNewName("");
    setNewPhone("");
    setNewOptIn(false);
    setShowAdd(false);
  }

  async function handleReminder(customer) {
    if (!onSendReminder) return;
    try {
      const res = await onSendReminder(customer.id);
      setReminderPreview(res);
    } catch (err) {
      setReminderPreview({ error: err.message, customer_name: customer.name });
    }
  }

  return (
    <div>
      <div className="stats-grid" style={{ marginBottom: 14 }}>
        <div className="card">
          <div className="stat-label">Customer Blocks</div>
          <div className="stat-val">
            {filteredCustomers.length}
            {filteredCustomers.length !== customers.length ? ` / ${customers.length}` : ""}
          </div>
        </div>
        <div className="card">
          <div className="stat-label">Shown Transactions</div>
          <div className="stat-val">{summary.totalTxCount}</div>
        </div>
        <div className="card">
          <div className="stat-label">All-Time Credit</div>
          <div className="stat-val credit">{formatINR(summary.totalCredit)}</div>
        </div>
        <div className="card">
          <div className="stat-label">Total Net Due</div>
          <div className="stat-val accent">{formatINR(summary.totalOutstanding)}</div>
        </div>
      </div>

      <div className="card" style={{ marginBottom: 16 }}>
        <div className="customer-ledgers-header">
          <div>
            <h2 style={{ margin: 0, fontSize: "1.08rem" }}>Every Customer Ledger</h2>
            <div style={{ fontSize: "0.8rem", color: "var(--muted)", marginTop: 2 }}>
              Individual khata blocks for every customer with their complete balance and transaction history.
            </div>
          </div>
          <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
            <a className="btn btn-secondary" href="/api/export/csv" style={{ textDecoration: "none" }}>
              <Download size={15} /> Export All CSV
            </a>
            {onCreateCustomer && (
              <button className="btn btn-primary" type="button" onClick={() => setShowAdd((v) => !v)}>
                <UserPlus size={15} /> Add Customer
              </button>
            )}
          </div>
        </div>

        <div className="customer-ledgers-controls">
          <div style={{ position: "relative", flex: "1 1 240px", minWidth: 0 }}>
            <Search size={16} style={{ position: "absolute", left: 12, top: 13, color: "var(--muted)" }} />
            <input
              className="text-input"
              style={{ paddingLeft: 36 }}
              placeholder="Search customer, phone, or transaction note..."
              value={search}
              onChange={(e) => setSearch(e.target.value)}
            />
          </div>

          <select
            className="text-input"
            style={{ width: "auto", minWidth: 185 }}
            aria-label="Sort customer ledgers"
            value={sortBy}
            onChange={(e) => setSortBy(e.target.value)}
          >
            <option value="name">Sort: Name (A–Z)</option>
            <option value="due_desc">Sort: Highest Due</option>
            <option value="tx_desc">Sort: Most Transactions</option>
            <option value="recent">Sort: Latest Activity</option>
          </select>
        </div>

        <div className="filter-pill-row">
          <button
            type="button"
            className={`filter-pill ${balanceFilter === "all" ? "active" : ""}`}
            onClick={() => setBalanceFilter("all")}
          >
            All Customers ({counts.all})
          </button>
          <button
            type="button"
            className={`filter-pill ${balanceFilter === "due" ? "active" : ""}`}
            onClick={() => setBalanceFilter("due")}
          >
            Due Balance ({counts.due})
          </button>
          <button
            type="button"
            className={`filter-pill ${balanceFilter === "settled" ? "active" : ""}`}
            onClick={() => setBalanceFilter("settled")}
          >
            Settled / Advance ({counts.settled})
          </button>
        </div>

        {showAdd && (
          <form onSubmit={handleAddCustomer} style={{ marginTop: 12, display: "grid", gap: 8 }}>
            <input
              className="text-input"
              placeholder="Customer full name (e.g. Anand Sharma)"
              value={newName}
              onChange={(e) => setNewName(e.target.value)}
              required
            />
            <input
              className="text-input"
              placeholder="Phone (encrypted at rest, optional)"
              value={newPhone}
              onChange={(e) => setNewPhone(e.target.value)}
            />
            <label style={{ display: "flex", alignItems: "center", gap: 8, fontSize: "0.85rem" }}>
              <input type="checkbox" checked={newOptIn} onChange={(e) => setNewOptIn(e.target.checked)} />
              Customer explicitly opted in to polite payment reminders
            </label>
            <button className="btn btn-primary" type="submit">
              Create Customer
            </button>
          </form>
        )}
      </div>

      {reminderPreview && (
        <div className="reply-banner">
          <div style={{ fontWeight: 700, fontSize: "0.85rem" }}>
            {reminderPreview.error
              ? `Reminder Blocked (${reminderPreview.customer_name})`
              : `Polite Reminder Sent to ${reminderPreview.customer_name}`}
          </div>
          <div style={{ fontSize: "0.88rem", marginTop: 4 }}>
            {reminderPreview.error || reminderPreview.message}
          </div>
        </div>
      )}

      {filteredCustomers.length === 0 ? (
        <div className="card" style={{ textAlign: "center", color: "var(--muted)", padding: 28 }}>
          No matching customers found.
        </div>
      ) : (
        <div className="customer-ledgers-grid">
          {filteredCustomers.map((c) => {
            const custTxs = txsByCustomer.get(Number(c.id)) || [];
            const isHighlighted =
              (highlightCustomerId != null && c.id === highlightCustomerId) ||
              (focusCustomerId != null && c.id === focusCustomerId);
            return (
              <section
                key={c.id}
                ref={(el) => {
                  if (el) blockRefs.current.set(c.id, el);
                  else blockRefs.current.delete(c.id);
                }}
                className={`card customer-ledger-block ${isHighlighted ? "live-flash" : ""}`}
                aria-label={`${c.name} ledger block`}
              >
                <CustomerLedgerCard
                  customer={c}
                  transactions={custTxs}
                  highlightTxIds={highlightTxIds}
                  isHighlighted={isHighlighted}
                  onUpdateCustomer={onUpdateCustomer}
                  onDeleteCustomer={onDeleteCustomer}
                  onAddTransaction={onManualTx}
                  onToggleOptIn={onToggleOptIn}
                  onSendReminder={onSendReminder ? handleReminder : undefined}
                />
              </section>
            );
          })}
        </div>
      )}
    </div>
  );
}
