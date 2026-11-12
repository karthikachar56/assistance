import { describe, expect, it } from "vitest";
import { resolveInitialAuthMode, validateLoginForm, validateSignUpForm } from "./authHelpers";

describe("authHelpers", () => {
  it("resolves login vs signup initial mode from pathname and hash", () => {
    expect(resolveInitialAuthMode("/signup", "")).toBe("signup");
    expect(resolveInitialAuthMode("/", "#/signup")).toBe("signup");
    expect(resolveInitialAuthMode("/", "#/login")).toBe("login");
    expect(resolveInitialAuthMode("/", "")).toBe("login");
  });

  it("validates login credentials clearly", () => {
    expect(validateLoginForm({ email: "bad-email", password: "abc" })).toMatch(/valid email/i);
    expect(validateLoginForm({ email: "shop@example.com", password: "" })).toMatch(/password/i);
    expect(validateLoginForm({ email: "shop@example.com", password: "secret" })).toBe("");
  });

  it("validates sign up fields including password length and confirmation match", () => {
    expect(
      validateSignUpForm({ name: "A", email: "shop@example.com", password: "secret", confirmPassword: "secret" })
    ).toMatch(/2 characters/i);
    expect(
      validateSignUpForm({ name: "Karthik", email: "shop@example.com", password: "123", confirmPassword: "123" })
    ).toMatch(/6 characters/i);
    expect(
      validateSignUpForm({ name: "Karthik", email: "shop@example.com", password: "secret1", confirmPassword: "secret2" })
    ).toMatch(/do not match/i);
    expect(
      validateSignUpForm({ name: "Karthik", email: "shop@example.com", password: "secret1", confirmPassword: "secret1" })
    ).toBe("");
  });
});

