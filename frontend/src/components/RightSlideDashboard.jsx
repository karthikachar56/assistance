import React, { useEffect, useRef } from "react";
import { BookOpen, LayoutGrid, Settings, Users, X } from "lucide-react";

export const NAV_SECTIONS = [
  {
    id: "home",
    label: "Khata",
    subtitle: "Today summary & live entries",
    Icon: BookOpen,
  },
  {
    id: "customers",
    label: "Customers",
    subtitle: "Balances, add, edit & reminders",
    Icon: Users,
  },
  {
    id: "ledgers",
    label: "Customer Ledgers",
    subtitle: "Individual blocks & all transactions",
    Icon: LayoutGrid,
  },
  {
    id: "settings",
    label: "Settings",
    subtitle: "Language, Gemini AI & privacy",
    Icon: Settings,
  },
];

/**
 * Right-side slide-over navigation drawer holding Khata, Customers, Customer Ledgers, and Settings.
 */
export function RightSlideDashboard({ open, activeTab, onSelectTab, onClose }) {
  const asideRef = useRef(null);

  useEffect(() => {
    if (!open) {
      if (
        typeof document !== "undefined" &&
        document.activeElement instanceof HTMLElement &&
        asideRef.current?.contains(document.activeElement)
      ) {
        document.activeElement.blur();
      }
      return undefined;
    }
    const onKeyDown = (e) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [open, onClose]);

  return (
    <>
      <div
        className={`nav-drawer-backdrop ${open ? "open" : ""}`}
        onClick={onClose}
        aria-hidden={!open}
      />

      <aside
        ref={asideRef}
        id="right-sliding-dashboard"
        className={`nav-drawer ${open ? "open" : ""}`}
        aria-label="Sliding dashboard menu"
        aria-hidden={!open}
        inert={!open ? "" : undefined}
      >
        <div className="nav-drawer-header">
          <div>
            <h2>Dashboard</h2>
            <small>Select a workspace view</small>
          </div>
          <button
            type="button"
            className="nav-drawer-close"
            onClick={onClose}
            aria-label="Close sliding dashboard"
          >
            <X size={18} />
          </button>
        </div>

        <nav className="nav-drawer-list" aria-label="Primary sections">
          {NAV_SECTIONS.map(({ id, label, subtitle, Icon }) => {
            const isActive = activeTab === id;
            return (
              <button
                key={id}
                type="button"
                className={`nav-tab nav-drawer-item ${isActive ? "active" : ""}`}
                onClick={() => onSelectTab(id)}
              >
                <span className="nav-drawer-icon">
                  <Icon size={18} />
                </span>
                <span className="nav-drawer-text">
                  <strong>{label}</strong>
                  <small>{subtitle}</small>
                </span>
                {isActive && <span className="nav-drawer-active-dot">Active</span>}
              </button>
            );
          })}
        </nav>

        <div className="nav-drawer-footer">
          Tip: Tap an option above or ask the Assistant by voice to switch screens automatically.
        </div>
      </aside>
    </>
  );
}

