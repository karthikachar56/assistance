/**
 * Deterministic voice/text UI navigation helper so spoken requests like
 * "show me total customer details", "open Ramesh Gowda", or "open settings"
 * navigate the 75% work pane immediately even during conversational turns.
 */

const TX_MUTATION_RE =
  /\b(\d+|udhaar|udhar|credit|credited|payment|paid|pay|debit|debited|deduct|diya|diye|jama|wapas|add|minus)\b/i;

export function inferNavigationFromText(text, customers = []) {
  const clean = (text || "").trim();
  if (!clean) return null;
  const low = clean.toLowerCase();

  // Do not hijack credit/payment recording turns as pure navigation
  if (TX_MUTATION_RE.test(low) && /\d/.test(low)) {
    return null;
  }

  // 1a. Dedicated all-customer ledger blocks page
  if (
    /(customer\s+ledgers?|all\s+ledgers?|open\s+ledgers?|ledgers?\s+page|all\s+khatas?|every\s+customer|each\s+customer|individual\s+customer\s+blocks?|sabhi\s+ka\s+khata|har\s+customer)/i.test(
      low
    )
  ) {
    return { tab: "ledgers", openCustomerId: null, openCustomerName: null };
  }

  // 1b. Explicit Total/All customers summary
  if (
    /(total\s+customer|all\s+customer|sabhi\s+customer|saare\s+customer|show\s+customers|list\s+customers|open\s+customers|customers\s+page|customers\s+tab)/i.test(
      low
    )
  ) {
    return { tab: "customers", openCustomerId: null, openCustomerName: null };
  }

  // 2. Explicit app tabs (Khata / Settings)
  if (/(open|show|go\s+to|kholo)?\s*settings\b/i.test(low)) {
    return { tab: "settings", openCustomerId: null, openCustomerName: null };
  }
  if (
    /(open\s+khata|show\s+khata|open\s+dashboard|show\s+dashboard|go\s+home|home\s+page|main\s+page|today'?s?\s+credit|credit\s+today|who\s+took\s+credit|today'?s?\s+payment|payments?\s+today|who\s+paid\s+today|total\s+outstanding)/i.test(
      low
    )
  ) {
    return { tab: "home", openCustomerId: null, openCustomerName: null };
  }

  // 3. Specific customer details/history/balance request (checked before generic "customer detail")
  const asksToView = /(show|tell|open|details|detail|history|khata|hisaab|hisab|balance|total|dikhao|kholo|batao)/i.test(
    low
  );
  if (asksToView && Array.isArray(customers) && customers.length > 0) {
    // Full name match first
    const byFull = [...customers]
      .sort((a, b) => (b.name?.length || 0) - (a.name?.length || 0))
      .find((c) => c.name && new RegExp(`\\b${c.name.toLowerCase().replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}\\b`).test(low));
    if (byFull) {
      return { tab: "customers", openCustomerId: byFull.id, openCustomerName: byFull.name };
    }
    // Unique first-name match
    const firstMatches = customers.filter((c) => {
      const first = (c.name || "").toLowerCase().split(/\s+/)[0];
      return first.length >= 3 && new RegExp(`\\b${first.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}\\b`).test(low);
    });
    if (firstMatches.length === 1) {
      return {
        tab: "customers",
        openCustomerId: firstMatches[0].id,
        openCustomerName: firstMatches[0].name,
      };
    }
  }

  // 4. Generic customer details without a specific customer name -> All Customers view
  if (/(customer\s+detail|customers\s+detail)/i.test(low)) {
    return { tab: "customers", openCustomerId: null, openCustomerName: null };
  }

  return null;
}
