"use client";

import { useId, useMemo, useState } from "react";

import {
  type RateRow,
  availableCurrencies,
  convert,
  defaultPair,
  formatMoney,
  formatRate,
  parseAmount,
  quote,
  sourceLabel,
} from "@/lib/data/converter";
import { formatDate } from "@/lib/data/helpers";

// Converter for a city page. The server page passes the latest conversion matrix (numbers
// only); everything here runs in the browser without any API call or key.
export function CurrencyConverter({
  rates,
  cityCurrency,
}: {
  rates: RateRow[];
  cityCurrency: string | null;
}) {
  const currencies = useMemo(() => availableCurrencies(rates), [rates]);
  const initial = useMemo(() => defaultPair(cityCurrency, currencies), [cityCurrency, currencies]);
  const [from, setFrom] = useState(initial.from);
  const [to, setTo] = useState(initial.to);
  const [amountText, setAmountText] = useState("100");
  const id = useId();

  if (currencies.length < 2) {
    return <p>Exchange rates are not available right now.</p>;
  }

  const amount = parseAmount(amountText);
  const q = quote(rates, from, to);
  const errorId = `${id}-error`;

  return (
    <div className="converter">
      <div className="converter-fields">
        <div className="field">
          <label htmlFor={`${id}-amount`}>Amount</label>
          <input
            id={`${id}-amount`}
            name="amount"
            inputMode="decimal"
            autoComplete="off"
            value={amountText}
            onChange={(e) => setAmountText(e.target.value)}
            aria-invalid={!amount.ok}
            aria-describedby={!amount.ok ? errorId : undefined}
          />
        </div>
        <div className="field">
          <label htmlFor={`${id}-from`}>From</label>
          <select id={`${id}-from`} value={from} onChange={(e) => setFrom(e.target.value)}>
            {currencies.map((c) => (
              <option key={c} value={c}>
                {c}
              </option>
            ))}
          </select>
        </div>
        <button
          type="button"
          className="button swap"
          onClick={() => {
            setFrom(to);
            setTo(from);
          }}
          aria-label={`Swap currencies: convert ${to} to ${from}`}
        >
          <span aria-hidden="true">&#8644;</span> Swap
        </button>
        <div className="field">
          <label htmlFor={`${id}-to`}>To</label>
          <select id={`${id}-to`} value={to} onChange={(e) => setTo(e.target.value)}>
            {currencies.map((c) => (
              <option key={c} value={c}>
                {c}
              </option>
            ))}
          </select>
        </div>
      </div>

      {!amount.ok && (
        <p id={errorId} className="field-error">
          {amount.error}
        </p>
      )}

      <div className="converter-result" aria-live="polite" data-testid="converter-result">
        {amount.ok && q ? (
          <>
            <p className="converter-amount">
              {formatMoney(amount.value, from)} = <strong>{formatMoney(convert(amount.value, q), to)}</strong>
            </p>
            <p className="small muted">
              1 {from} = {formatRate(q.rate)} {to}
              {q.rateDate && (
                <>
                  {" "}
                  · rate of <time dateTime={q.rateDate}>{formatDate(q.rateDate)}</time>
                </>
              )}
              {q.sources.length > 0 && <> · source: {sourceLabel(q)}</>}
            </p>
          </>
        ) : amount.ok ? (
          <p>No rate available for {from} to {to}.</p>
        ) : null}
      </div>
    </div>
  );
}
