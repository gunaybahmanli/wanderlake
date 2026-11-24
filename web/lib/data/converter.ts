// Currency conversion on the published conversion matrix (gold.fct_exchange_rate, latest
// date). Pure functions: used by the CurrencyConverter client component and unit tested.
import type { ExchangeRate } from "./types";

/** Only what the browser needs: passed from the server page to the client component. */
export type RateRow = Pick<
  ExchangeRate,
  | "from_currency"
  | "to_currency"
  | "rate"
  | "from_rate_date"
  | "to_rate_date"
  | "source_from"
  | "source_to"
>;

export const DEFAULT_FROM = "AZN";
export const FALLBACK_TO = "USD";
export const MAX_AMOUNT = 1e12;

export function toRateRows(rates: ExchangeRate[]): RateRow[] {
  return rates.map(
    ({ from_currency, to_currency, rate, from_rate_date, to_rate_date, source_from, source_to }) => ({
      from_currency,
      to_currency,
      rate,
      from_rate_date,
      to_rate_date,
      source_from,
      source_to,
    }),
  );
}

/** Every currency that appears in the matrix, sorted. */
export function availableCurrencies(rates: RateRow[]): string[] {
  return [...new Set(rates.flatMap((r) => [r.from_currency, r.to_currency]))].sort();
}

/**
 * Default pair for a city: from AZN to the city's currency. When the city already uses AZN
 * (Baku), convert to USD instead. Falls back to the first available currencies if AZN or the
 * target is not in the matrix.
 */
export function defaultPair(
  cityCurrency: string | null,
  currencies: string[],
): { from: string; to: string } {
  const has = (c: string | null): c is string => !!c && currencies.includes(c);
  const from = has(DEFAULT_FROM) ? DEFAULT_FROM : (currencies[0] ?? DEFAULT_FROM);
  let to = has(cityCurrency) && cityCurrency !== from ? cityCurrency : FALLBACK_TO;
  if (!has(to) || to === from) to = currencies.find((c) => c !== from) ?? from;
  return { from, to };
}

export type Quote = {
  from: string;
  to: string;
  rate: number; // units of `to` per 1 `from`
  rateDate: string | null; // the older of the two rate dates (null for identical currencies)
  sources: { currency: string; source: string }[];
};

/** The rate for a pair, or null when the matrix has no such pair. Same currency: rate 1. */
export function quote(rates: RateRow[], from: string, to: string): Quote | null {
  if (from === to) return { from, to, rate: 1, rateDate: null, sources: [] };
  const row = rates.find((r) => r.from_currency === from && r.to_currency === to);
  if (!row) return null;
  const rateDate = row.from_rate_date < row.to_rate_date ? row.from_rate_date : row.to_rate_date;
  const sources = [
    { currency: from, source: row.source_from },
    { currency: to, source: row.source_to },
  ].filter((s): s is { currency: string; source: string } => !!s.source);
  return { from, to, rate: row.rate, rateDate, sources };
}

export function convert(amount: number, q: Quote): number {
  return amount * q.rate;
}

const SOURCE_NAMES: Record<string, string> = { ecb: "ECB", cbar: "CBAR" };

/** "CBAR (AZN), ECB (USD)", or "ECB" when both sides come from the same source. */
export function sourceLabel(q: Quote): string {
  const names = q.sources.map((s) => ({
    ...s,
    name: SOURCE_NAMES[s.source] ?? s.source.toUpperCase(),
  }));
  if (names.length === 0) return "";
  if (new Set(names.map((n) => n.name)).size === 1) return names[0].name;
  return names.map((n) => `${n.name} (${n.currency})`).join(", ");
}

export type AmountResult = { ok: true; value: number } | { ok: false; error: string };

/** Parse a user-typed amount: digits with an optional decimal point or comma, 0 to 1e12. */
export function parseAmount(input: string): AmountResult {
  const text = input.trim().replace(/[\s_]/g, "");
  if (text === "") return { ok: false, error: "Enter an amount." };
  if (!/^\d+([.,]\d+)?$/.test(text)) {
    return { ok: false, error: "Use digits only, with an optional decimal point (e.g. 150.50)." };
  }
  const value = Number(text.replace(",", "."));
  if (value > MAX_AMOUNT) return { ok: false, error: "Enter an amount up to 1,000,000,000,000." };
  return { ok: true, value };
}

/** Money in a currency, with that currency's usual decimals (JPY and KRW have none). */
export function formatMoney(value: number, currency: string): string {
  return new Intl.NumberFormat("en-GB", { style: "currency", currency }).format(value);
}

/** An exchange rate with enough significant digits for small and large rates alike. */
export function formatRate(rate: number): string {
  return new Intl.NumberFormat("en-GB", { maximumSignificantDigits: 6 }).format(rate);
}
