import React, { useEffect, useState } from "react";
import { Check, X } from "lucide-react";

const CUSTOMER_LANGUAGES = [
  { code: "hi-IN", label: "Hindi / Hinglish (hi-IN)" },
  { code: "kn-IN", label: "Kannada / Kanglish (kn-IN)" },
  { code: "ta-IN", label: "Tamil / Tanglish (ta-IN)" },
  { code: "te-IN", label: "Telugu / Tenglish (te-IN)" },
  { code: "en-IN", label: "Indian English (en-IN)" },
];

export function CustomerEditForm({ customer, onSave, onCancel }) {
  const [name, setName] = useState(customer?.name || "");
  const [phone, setPhone] = useState(customer?.phone || "");
  const [preferredLanguage, setPreferredLanguage] = useState(customer?.preferred_language || "hi-IN");
  const [reminderOptIn, setReminderOptIn] = useState(Boolean(customer?.reminder_opt_in));
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    setName(customer?.name || "");
    setPhone(customer?.phone || "");
    setPreferredLanguage(customer?.preferred_language || "hi-IN");
    setReminderOptIn(Boolean(customer?.reminder_opt_in));
  }, [customer?.id, customer?.name, customer?.phone, customer?.preferred_language, customer?.reminder_opt_in]);

  async function handleSubmit(e) {
    e.preventDefault();
    if (!name.trim() || saving) return;
    setSaving(true);
    try {
      await onSave({
        name: name.trim(),
        phone: phone.trim(),
        preferred_language: preferredLanguage,
        reminder_opt_in: reminderOptIn,
      });
    } finally {
      setSaving(false);
    }
  }

  return (
    <form
      onSubmit={handleSubmit}
      style={{
        display: "grid",
        gap: 8,
        marginTop: 10,
        padding: 12,
        borderRadius: 12,
        background: "var(--bg)",
        border: "1px solid var(--border)",
      }}
    >
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(170px, 1fr))", gap: 8 }}>
        <label style={{ display: "grid", gap: 4, fontSize: "0.78rem", fontWeight: 600 }}>
          Full Name
          <input
            className="text-input"
            value={name}
            onChange={(e) => setName(e.target.value)}
            required
            placeholder="Customer full name"
          />
        </label>
        <label style={{ display: "grid", gap: 4, fontSize: "0.78rem", fontWeight: 600 }}>
          Phone (Encrypted)
          <input
            className="text-input"
            value={phone}
            onChange={(e) => setPhone(e.target.value)}
            placeholder="Optional phone number"
          />
        </label>
        <label style={{ display: "grid", gap: 4, fontSize: "0.78rem", fontWeight: 600 }}>
          Language
          <select
            className="text-input"
            value={preferredLanguage}
            onChange={(e) => setPreferredLanguage(e.target.value)}
          >
            {CUSTOMER_LANGUAGES.map((l) => (
              <option key={l.code} value={l.code}>
                {l.label}
              </option>
            ))}
          </select>
        </label>
      </div>
      <label style={{ display: "flex", alignItems: "center", gap: 8, fontSize: "0.82rem" }}>
        <input
          type="checkbox"
          checked={reminderOptIn}
          onChange={(e) => setReminderOptIn(e.target.checked)}
        />
        Customer opted in to polite payment reminders
      </label>
      <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
        <button className="btn btn-primary" type="submit" disabled={saving || !name.trim()}>
          <Check size={14} /> {saving ? "Saving..." : "Save Changes"}
        </button>
        <button className="btn btn-secondary" type="button" onClick={onCancel} disabled={saving}>
          <X size={14} /> Cancel
        </button>
      </div>
    </form>
  );
}

