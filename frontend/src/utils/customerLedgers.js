/**
 * Pure helpers to group transactions per customer, filter, sort, and summarize
 * customer ledger blocks on the All Customer Ledgers workspace view.
 */

export function groupTransactionsByCustomerId(transactions = []) {
  const map = new Map();
  for (const tx of transactions) {
    const cid = Number(tx.customer_id);
    if (!map.has(cid)) map.set(cid, []);
    map.get(cid).push(tx);
  }
  return map;
}

export function countCustomerBalanceGroups(customers = []) {
  let due = 0;
  let settled = 0;
  for (const c of customers) {
    if (Number(c.outstanding_balance || 0) > 0) due += 1;
    else settled += 1;
  }
  return { all: customers.length, due, settled };
}

export function filterAndSortCustomerLedgers(
  customers = [],
  txsByCustomer = new Map(),
  { search = "", balanceFilter = "all", sortBy = "name" } = {}
) {
  const q = search.trim().toLowerCase();
  const list = customers.filter((c) => {
    const bal = Number(c.outstanding_balance || 0);
    if (balanceFilter === "due" && bal <= 0) return false;
    if (balanceFilter === "settled" && bal > 0) return false;
    if (!q) return true;
    if ((c.name || "").toLowerCase().includes(q)) return true;
    if ((c.phone || "").toLowerCase().includes(q)) return true;
    const txs = txsByCustomer.get(Number(c.id)) || [];
    return txs.some(
      (tx) =>
        (tx.note || "").toLowerCase().includes(q) ||
        (tx.transaction_date || "").toLowerCase().includes(q) ||
        String(tx.amount).includes(q)
    );
  });

  list.sort((a, b) => {
    const aTxs = txsByCustomer.get(Number(a.id)) || [];
    const bTxs = txsByCustomer.get(Number(b.id)) || [];
    if (sortBy === "due_desc") {
      const diff = Number(b.outstanding_balance || 0) - Number(a.outstanding_balance || 0);
      if (diff !== 0) return diff;
    } else if (sortBy === "tx_desc") {
      const diff = bTxs.length - aTxs.length;
      if (diff !== 0) return diff;
    } else if (sortBy === "recent") {
      const aTop = aTxs[0];
      const bTop = bTxs[0];
      const aKey = aTop ? `${aTop.transaction_date}-${String(aTop.id).padStart(8, "0")}` : "";
      const bKey = bTop ? `${bTop.transaction_date}-${String(bTop.id).padStart(8, "0")}` : "";
      if (aKey !== bKey) return bKey.localeCompare(aKey);
    }
    return (a.name || "").localeCompare(b.name || "");
  });

  return list;
}

export function summarizeCustomerLedgers(customers = [], txsByCustomer = new Map()) {
  let totalCredit = 0;
  let totalOutstanding = 0;
  let totalTxCount = 0;
  for (const c of customers) {
    totalCredit += Number(c.total_credit || 0);
    totalOutstanding += Number(c.outstanding_balance || 0);
    totalTxCount += (txsByCustomer.get(Number(c.id)) || []).length;
  }
  return { totalCredit, totalOutstanding, totalTxCount };
}

