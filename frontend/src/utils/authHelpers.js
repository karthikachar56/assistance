/**
 * Route-mode resolution and client-side form validation helpers for Login and Sign Up pages.
 */

export function resolveInitialAuthMode(pathname = "", hash = "") {
  const target = `${pathname || ""} ${hash || ""}`.toLowerCase();
  if (target.includes("signup") || target.includes("sign-up") || target.includes("register")) {
    return "signup";
  }
  return "login";
}

export function isValidEmail(email = "") {
  const clean = (email || "").trim();
  const atIdx = clean.indexOf("@");
  if (atIdx <= 0 || clean.includes(" ")) return false;
  const domain = clean.slice(atIdx + 1);
  return domain.includes(".") && !domain.startsWith(".") && !domain.endsWith(".");
}

export function validateLoginForm({ email = "", password = "" }) {
  if (!isValidEmail(email)) {
    return "Please enter a valid email address.";
  }
  if (!password) {
    return "Please enter your password.";
  }
  return "";
}

export function validateSignUpForm({
  name = "",
  email = "",
  password = "",
  confirmPassword = "",
}) {
  if ((name || "").trim().length < 2) {
    return "Full name must be at least 2 characters.";
  }
  if (!isValidEmail(email)) {
    return "Please enter a valid email address.";
  }
  if ((password || "").length < 6) {
    return "Password must be at least 6 characters.";
  }
  if (password !== confirmPassword) {
    return "Passwords do not match.";
  }
  return "";
}

