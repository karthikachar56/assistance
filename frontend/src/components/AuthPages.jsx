import React, { useState } from "react";
import { Eye, EyeOff, LogIn, Mic, ShieldCheck, Store, UserPlus, Users } from "lucide-react";
import { validateLoginForm, validateSignUpForm } from "../utils/authHelpers";

const SIGNUP_LANGUAGES = [
  { code: "hi-IN", label: "Hindi / Hinglish (hi-IN)" },
  { code: "kn-IN", label: "Kannada (kn-IN)" },
  { code: "ta-IN", label: "Tamil / Tanglish (ta-IN)" },
  { code: "te-IN", label: "Telugu / Tenglish (te-IN)" },
  { code: "en-IN", label: "Indian English (en-IN)" },
];

function PasswordInput({ id, label, value, onChange, placeholder, autoComplete }) {
  const [visible, setVisible] = useState(false);
  return (
    <label className="auth-field">
      <span>{label}</span>
      <div className="auth-password-wrap">
        <input
          id={id}
          className="text-input"
          type={visible ? "text" : "password"}
          value={value}
          onChange={(e) => onChange(e.target.value)}
          placeholder={placeholder}
          autoComplete={autoComplete}
          required
        />
        <button
          type="button"
          className="auth-eye-btn"
          onClick={() => setVisible((v) => !v)}
          aria-label={visible ? `Hide ${label}` : `Show ${label}`}
        >
          {visible ? <EyeOff size={16} /> : <Eye size={16} />}
        </button>
      </div>
    </label>
  );
}

/**
 * Login page rendered before entering the Voice Khata workspace.
 */
export function LoginPage({ onSubmit, onSwitchToSignUp, loading = false, error = "" }) {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [localError, setLocalError] = useState("");

  async function handleSubmit(e) {
    e.preventDefault();
    const validationErr = validateLoginForm({ email, password });
    if (validationErr) {
      setLocalError(validationErr);
      return;
    }
    setLocalError("");
    await onSubmit({ email: email.trim(), password });
  }

  const displayError = localError || error;

  return (
    <form className="auth-form" onSubmit={handleSubmit} noValidate>
      <div className="auth-card-head">
        <h2>Welcome back</h2>
        <p>Sign in to open your Voice Khata dashboard and customer ledgers.</p>
      </div>

      {displayError && (
        <div className="auth-error-banner" role="alert">
          {displayError}
        </div>
      )}

      <label className="auth-field">
        <span>Email Address</span>
        <input
          id="login-email"
          className="text-input"
          type="email"
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          placeholder="you@example.com"
          autoComplete="email"
          required
        />
      </label>

      <PasswordInput
        id="login-password"
        label="Password"
        value={password}
        onChange={setPassword}
        placeholder="Enter your password"
        autoComplete="current-password"
      />

      <button className="btn btn-primary auth-submit-btn" type="submit" disabled={loading}>
        <LogIn size={16} /> {loading ? "Signing in..." : "Sign In to Dashboard"}
      </button>

      <div className="auth-switch-row">
        <span>New to Voice Khata?</span>
        <button type="button" className="auth-link-btn" onClick={onSwitchToSignUp}>
          Create an account
        </button>
      </div>
    </form>
  );
}

/**
 * Sign Up page creating a shopkeeper account before entering the dashboard.
 */
export function SignUpPage({ onSubmit, onSwitchToLogin, loading = false, error = "" }) {
  const [name, setName] = useState("");
  const [shopName, setShopName] = useState("");
  const [email, setEmail] = useState("");
  const [phone, setPhone] = useState("");
  const [preferredLanguage, setPreferredLanguage] = useState("hi-IN");
  const [password, setPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [localError, setLocalError] = useState("");

  async function handleSubmit(e) {
    e.preventDefault();
    const validationErr = validateSignUpForm({ name, email, password, confirmPassword });
    if (validationErr) {
      setLocalError(validationErr);
      return;
    }
    setLocalError("");
    await onSubmit({
      name: name.trim(),
      shop_name: shopName.trim() || null,
      email: email.trim(),
      phone: phone.trim() || null,
      preferred_language: preferredLanguage,
      password,
    });
  }

  const displayError = localError || error;

  return (
    <form className="auth-form" onSubmit={handleSubmit} noValidate>
      <div className="auth-card-head">
        <h2>Create your Khata account</h2>
        <p>Sign up to access your kirana voice ledger and per-customer accounts.</p>
      </div>

      {displayError && (
        <div className="auth-error-banner" role="alert">
          {displayError}
        </div>
      )}

      <div className="auth-two-col">
        <label className="auth-field">
          <span>Full Name</span>
          <input
            id="signup-name"
            className="text-input"
            type="text"
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="e.g. Karthik Achar"
            autoComplete="name"
            required
          />
        </label>

        <label className="auth-field">
          <span>Shop / Store Name (Optional)</span>
          <input
            id="signup-shop"
            className="text-input"
            type="text"
            value={shopName}
            onChange={(e) => setShopName(e.target.value)}
            placeholder="e.g. Sri Lakshmi Kirana"
            autoComplete="organization"
          />
        </label>
      </div>

      <label className="auth-field">
        <span>Email Address</span>
        <input
          id="signup-email"
          className="text-input"
          type="email"
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          placeholder="you@example.com"
          autoComplete="email"
          required
        />
      </label>

      <div className="auth-two-col">
        <label className="auth-field">
          <span>Phone (Optional, Encrypted)</span>
          <input
            id="signup-phone"
            className="text-input"
            type="tel"
            value={phone}
            onChange={(e) => setPhone(e.target.value)}
            placeholder="+91-98..."
            autoComplete="tel"
          />
        </label>

        <label className="auth-field">
          <span>Preferred Language</span>
          <select
            id="signup-language"
            className="text-input"
            value={preferredLanguage}
            onChange={(e) => setPreferredLanguage(e.target.value)}
          >
            {SIGNUP_LANGUAGES.map((l) => (
              <option key={l.code} value={l.code}>
                {l.label}
              </option>
            ))}
          </select>
        </label>
      </div>

      <div className="auth-two-col">
        <PasswordInput
          id="signup-password"
          label="Password (min 6 chars)"
          value={password}
          onChange={setPassword}
          placeholder="Create password"
          autoComplete="new-password"
        />

        <PasswordInput
          id="signup-confirm-password"
          label="Confirm Password"
          value={confirmPassword}
          onChange={setConfirmPassword}
          placeholder="Re-enter password"
          autoComplete="new-password"
        />
      </div>

      <button className="btn btn-primary auth-submit-btn" type="submit" disabled={loading}>
        <UserPlus size={16} /> {loading ? "Creating account..." : "Create Account & Enter Dashboard"}
      </button>

      <div className="auth-switch-row">
        <span>Already have an account?</span>
        <button type="button" className="auth-link-btn" onClick={onSwitchToLogin}>
          Sign in here
        </button>
      </div>
    </form>
  );
}

/**
 * Full-screen authentication shell presenting Login and Sign Up pages
 * prior to entering the Voice Khata dashboard.
 */
export function AuthGateway({
  mode,
  onChangeMode,
  onLogin,
  onSignUp,
  loading = false,
  error = "",
  statusInfo = null,
}) {
  return (
    <div className="auth-shell">
      <aside className="auth-hero" aria-label="Voice Khata Overview">
        <div>
          <div className="brand" style={{ marginBottom: 22 }}>
            <div className="brand-mark">₹</div>
            <div>
              <h1 style={{ color: "#f8fafc", margin: 0, fontSize: "1.3rem" }}>Voice Khata</h1>
              <small style={{ color: "#94a3b8" }}>Kirana AI Digital Accountant · Gemini 3.8 / 4 Argon</small>
            </div>
          </div>

          <h2 className="auth-hero-title">
            Your voice-first kirana ledger, protected before you open the register.
          </h2>
          <p className="auth-hero-sub">
            Sign in or create your shopkeeper account to record daily udhaar, inspect every customer&apos;s
            individual block ledger, and manage your store securely.
          </p>

          <ul className="auth-feature-list">
            <li>
              <Mic size={16} />
              <span>Speak naturally in Hindi, Kannada, Tamil, Telugu, or Indian English</span>
            </li>
            <li>
              <Users size={16} />
              <span>Browse per-customer ledger blocks with complete transaction history</span>
            </li>
            <li>
              <Store size={16} />
              <span>Track today&apos;s credit, collections, and export CSV reports in one tap</span>
            </li>
            <li>
              <ShieldCheck size={16} />
              <span>Salted scrypt passwords, signed sessions &amp; AES-256-GCM encryption at rest</span>
            </li>
          </ul>
        </div>
      </aside>

      <main className="auth-main">
        <div className="card auth-card">
          <div className="auth-mode-tabs" role="tablist" aria-label="Authentication Pages">
            <button
              type="button"
              role="tab"
              aria-selected={mode === "login"}
              className={`auth-mode-tab ${mode === "login" ? "active" : ""}`}
              onClick={() => onChangeMode("login")}
            >
              <LogIn size={15} /> Login Page
            </button>
            <button
              type="button"
              role="tab"
              aria-selected={mode === "signup"}
              className={`auth-mode-tab ${mode === "signup" ? "active" : ""}`}
              onClick={() => onChangeMode("signup")}
            >
              <UserPlus size={15} /> Sign Up Page
            </button>
          </div>

          {mode === "signup" ? (
            <SignUpPage
              onSubmit={onSignUp}
              onSwitchToLogin={() => onChangeMode("login")}
              loading={loading}
              error={error}
            />
          ) : (
            <LoginPage
              onSubmit={onLogin}
              onSwitchToSignUp={() => onChangeMode("signup")}
              loading={loading}
              error={error}
            />
          )}
        </div>
      </main>
    </div>
  );
}

