import { describe, expect, it } from "vitest";
import {
  countCustomerBalanceGroups,
  filterAndSortCustomerLedgers,
  groupTransactionsByCustomerId,
  summarizeCustomerLedgers,
} from "./customerLedgers";

const CUSTOMERS = [
  { id: 1, name: "Ramesh Gowda", phone: "9800000001", total_credit: 500, total_payment: 200, outstanding_balance: 300 },
  { id: 2, name: "Lakshmi Devi", phone: "", total_credit: 400, total_payment: 400, outstanding_balance: 0 },
  { id: 3, name: "Suresh Patel", phone: "9800000003", total_credit: 900, total_payment: 100, outstanding_balance: 800 },
];

const TRANSACTIONS = [
  { id: 11, customer_id: 1, amount: 500, tx_type: "credit", transaction_date: "2026-11-12", note: "Rice bag" },
  { id: 10, customer_id: 1, amount: 200, tx_type: "payment", transaction_date: "2026-11-11", note: "Cash" },
  { id: 9, customer_id: 3, amount: 900, tx_type: "credit", transaction_date: "2026-11-10", note: "Oil & sugar" },
];

describe("customerLedgers helpers", () => {
  it("groups transactions by customer_id and computes balance counts", () => {
    const grouped = groupTransactionsByCustomerId(TRANSACTIONS);
    expect(grouped.get(1)?.length).toBe(2);
    expect(grouped.get(3)?.length).toBe(1);
    expect(grouped.get(2)).toBeUndefined();
    expect(countCustomerBalanceGroups(CUSTOMERS)).toEqual({ all: 3, due: 2, settled: 1 });
  });

  it("filters by due status, matches transaction notes, and sorts by due/tx/recent", () => {
    const grouped = groupTransactionsByCustomerId(TRANSACTIONS);

    const byNote = filterAndSortCustomerLedgers(CUSTOMERS, grouped, { search: "sugar" });
    expect(byNote.map((c) => c.name)).toEqual(["Suresh Patel"]);

    const dueSorted = filterAndSortCustomerLedgers(CUSTOMERS, grouped, {
      balanceFilter: "due",
      sortBy: "due_desc",
    });
    expect(dueSorted.map((c) => c.name)).toEqual(["Suresh Patel", "Ramesh Gowda"]);

    const txSorted = filterAndSortCustomerLedgers(CUSTOMERS, grouped, { sortBy: "tx_desc" });
    expect(txSorted.map((c) => c.name)).toEqual(["Ramesh Gowda", "Suresh Patel", "Lakshmi Devi"]);

    const recentSorted = filterAndSortCustomerLedgers(CUSTOMERS, grouped, { sortBy: "recent" });
    expect(recentSorted.map((c) => c.name)).toEqual(["Ramesh Gowda", "Suresh Patel", "Lakshmi Devi"]);
  });

  it("summarizes totals across displayed customer blocks", () => {
    const grouped = groupTransactionsByCustomerId(TRANSACTIONS);
    expect(summarizeCustomerLedgers(CUSTOMERS, grouped)).toEqual({
      totalCredit: 1800,
      totalOutstanding: 1100,
      totalTxCount: 3,
    });
  });
});

