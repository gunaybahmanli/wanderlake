import { describe, expect, it } from "vitest";

import {
  availableCurrencies,
  convert,
  defaultPair,
  formatMoney,
  formatRate,
  parseAmount,
  quote,
  sourceLabel,
  toRateRows,
} from "./converter";
import { fixtureData } from "./fixtures";

const rates = toRateRows(fixtureData.rates);
const currencies = availableCurrencies(rates);

describe("defaults", () => {
  it("lists every currency in the matrix", () => {
    expect(currencies).toEqual(["AZN", "EUR", "JPY", "USD"]);
  });

  it("converts from AZN to the city's currency", () => {
    expect(defaultPair("JPY", currencies)).toEqual({ from: "AZN", to: "JPY" });
    expect(defaultPair("EUR", currencies)).toEqual({ from: "AZN", to: "EUR" });
  });

  it("uses USD for Baku, whose currency is already AZN", () => {
    expect(defaultPair("AZN", currencies)).toEqual({ from: "AZN", to: "USD" });
  });

  it("falls back to USD when the city's currency is not in the matrix", () => {
    expect(defaultPair("THB", currencies)).toEqual({ from: "AZN", to: "USD" });
    expect(defaultPair(null, currencies)).toEqual({ from: "AZN", to: "USD" });
  });
});

describe("conversion math", () => {
  it("converts AZN pairs with the matrix rate", () => {
    const aznUsd = quote(rates, "AZN", "USD")!;
    expect(aznUsd.rate).toBeCloseTo(0.5882353, 6); // CBAR peg: 1.7 AZN per USD
    expect(convert(170, aznUsd)).toBeCloseTo(100, 6);
    expect(convert(100, quote(rates, "USD", "AZN")!)).toBeCloseTo(170, 6);
    expect(convert(1, quote(rates, "AZN", "JPY")!)).toBeCloseTo(0.535733419051 / 0.0061, 6);
  });

  it("is consistent with the inverse pair for every pair", () => {
    for (const from of currencies) {
      for (const to of currencies) {
        const there = quote(rates, from, to)!;
        const back = quote(rates, to, from)!;
        expect(Math.abs(there.rate * back.rate - 1)).toBeLessThan(1e-9);
        expect(convert(convert(250, there), back)).toBeCloseTo(250, 6);
      }
    }
  });

  it("uses rate 1 for the same currency and null for an unknown pair", () => {
    expect(quote(rates, "EUR", "EUR")).toMatchObject({ rate: 1, rateDate: null, sources: [] });
    expect(quote(rates, "AZN", "XXX")).toBeNull();
  });

  it("reports the older rate date and the source of each side", () => {
    const q = quote(rates, "AZN", "JPY")!;
    expect(q.rateDate).toBe("2026-11-20");
    expect(sourceLabel(q)).toBe("CBAR (AZN), ECB (JPY)");
    expect(sourceLabel(quote(rates, "AZN", "USD")!)).toBe("CBAR"); // direct CBAR rate
    expect(sourceLabel(quote(rates, "EUR", "AZN")!)).toBe("CBAR");
    expect(sourceLabel(quote(rates, "EUR", "USD")!)).toBe("ECB");
  });
});

describe("amount input", () => {
  it("accepts plain numbers with a decimal point or comma", () => {
    expect(parseAmount("100")).toEqual({ ok: true, value: 100 });
    expect(parseAmount(" 150.50 ")).toEqual({ ok: true, value: 150.5 });
    expect(parseAmount("2,5")).toEqual({ ok: true, value: 2.5 });
    expect(parseAmount("0")).toEqual({ ok: true, value: 0 });
  });

  it("rejects empty, negative, non-numeric and too large amounts", () => {
    expect(parseAmount("")).toMatchObject({ ok: false });
    expect(parseAmount("-5")).toMatchObject({ ok: false });
    expect(parseAmount("12abc")).toMatchObject({ ok: false });
    expect(parseAmount("1e5")).toMatchObject({ ok: false });
    expect(parseAmount("2000000000000")).toMatchObject({ ok: false });
  });
});

describe("formatting", () => {
  it("formats money with each currency's decimals", () => {
    expect(formatMoney(1234.5, "USD")).toBe("US$1,234.50");
    expect(formatMoney(1234.5, "JPY")).toBe("JP¥1,235");
    expect(formatMoney(10, "AZN").replace(/\s/g, " ")).toBe("AZN 10.00"); // Intl uses a no-break space
  });

  it("formats rates with six significant digits", () => {
    expect(formatRate(0.588235294)).toBe("0.588235");
    expect(formatRate(87.8251)).toBe("87.8251");
  });
});
