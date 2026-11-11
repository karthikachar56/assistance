import React from "react";
import { ShieldCheck } from "lucide-react";

export function ConsentBanner({ disclosurePreview, onAccept }) {
  return (
    <div className="consent-banner" role="region" aria-label="AI Disclosure and DPDP Consent">
      <div style={{ flex: "1 1 280px" }}>
        <div style={{ display: "flex", alignItems: "center", gap: 6, fontWeight: 700, fontSize: "0.9rem" }}>
          <ShieldCheck size={16} color="#fb923c" />
          AI Disclosure &amp; DPDP Audio Notice
        </div>
        <div style={{ fontSize: "0.78rem", color: "#d6d3d1", marginTop: 4 }}>
          {disclosurePreview} Ledger balances stay encrypted locally (AES-256-GCM); cloud speech APIs process audio only when enabled.
        </div>
      </div>
      <button className="btn btn-primary" onClick={onAccept}>
        Accept &amp; Continue
      </button>
    </div>
  );
}

