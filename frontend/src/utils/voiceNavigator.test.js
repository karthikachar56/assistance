import { describe, expect, it } from "vitest";
import { inferNavigationFromText } from "./voiceNavigator";

const SAMPLE_CUSTOMERS = [
  { id: 1, name: "Ramesh Gowda" },
  { id: 2, name: "Ramesh Kumar" },
  { id: 3, name: "Lakshmi Devi" },
];

describe("inferNavigationFromText", () => {
  it("navigates to customers summary when asking for total customer details", () => {
    expect(inferNavigationFromText("Tell me the total customer details", SAMPLE_CUSTOMERS)).toEqual({
      tab: "customers",
      openCustomerId: null,
      openCustomerName: null,
    });
    expect(inferNavigationFromText("Show all customers", SAMPLE_CUSTOMERS)).toEqual({
      tab: "customers",
      openCustomerId: null,
      openCustomerName: null,
    });
  });

  it("opens specific customer ledger details on full or unique first name", () => {
    expect(inferNavigationFromText("Show Ramesh Gowda details", SAMPLE_CUSTOMERS)).toEqual({
      tab: "customers",
      openCustomerId: 1,
      openCustomerName: "Ramesh Gowda",
    });
    expect(inferNavigationFromText("Tell me Ramesh Gowda customer details", SAMPLE_CUSTOMERS)).toEqual({
      tab: "customers",
      openCustomerId: 1,
      openCustomerName: "Ramesh Gowda",
    });
    expect(inferNavigationFromText("Open Lakshmi khata", SAMPLE_CUSTOMERS)).toEqual({
      tab: "customers",
      openCustomerId: 3,
      openCustomerName: "Lakshmi Devi",
    });
  });

  it("opens Khata and Settings pages without Voice Log option", () => {
    expect(inferNavigationFromText("Open settings", SAMPLE_CUSTOMERS)?.tab).toBe("settings");
    expect(inferNavigationFromText("Open dashboard", SAMPLE_CUSTOMERS)?.tab).toBe("home");
    expect(inferNavigationFromText("Who took credit today?", SAMPLE_CUSTOMERS)?.tab).toBe("home");
    expect(inferNavigationFromText("Show voice log", SAMPLE_CUSTOMERS)).toBeNull();
  });

  it("does not hijack live credit/debit ledger transactions as pure navigation", () => {
    expect(inferNavigationFromText("Add 340 credit to Ramesh Gowda", SAMPLE_CUSTOMERS)).toBeNull();
    expect(inferNavigationFromText("Ramesh Kumar paid 250 yesterday", SAMPLE_CUSTOMERS)).toBeNull();
  });
});
