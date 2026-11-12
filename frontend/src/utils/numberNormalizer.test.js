import { describe, expect, it } from "vitest";
import { formatINR, previewSpokenAmount } from "./numberNormalizer.js";

describe("previewSpokenAmount", () => {
  it("parses Hinglish, English colloquial, Indic digits, fractions, and Kannada amounts", () => {
    expect(previewSpokenAmount("Ramesh ka teen sau chaalis udhaar")).toBe(340);
    expect(previewSpokenAmount("three-forty")).toBe(340);
    expect(previewSpokenAmount("chaar sau tees")).toBe(430);
    expect(previewSpokenAmount("Ramesh ka 340 udhaar")).toBe(340);
    expect(previewSpokenAmount("paanch sau")).toBe(500);
    expect(previewSpokenAmount("muunnooru nalavattu")).toBe(340);
    expect(previewSpokenAmount("Lakshmi ge nalknoora aivattu haaku")).toBe(450);
    expect(previewSpokenAmount("Mohan ka sava sau udhaar")).toBe(125);
    expect(previewSpokenAmount("Suresh dedh sau payment")).toBe(150);
    expect(previewSpokenAmount("Ramesh ka ३४० udhaar")).toBe(340);
    expect(previewSpokenAmount("Suresh 2.5k jama")).toBe(2500);
    expect(previewSpokenAmount("do sau pachaas")).toBe(250);
    expect(previewSpokenAmount("so good customer")).toBeNull();
    expect(previewSpokenAmount("Hello, explain briefly what you can do")).toBeNull();
  });

  it("formats INR values cleanly", () => {
    expect(formatINR(2450)).toContain("2,450");
  });
});
