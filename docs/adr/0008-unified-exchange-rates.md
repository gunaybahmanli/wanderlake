# ADR-0008: Unified exchange rates: ECB primary, CBAR for missing currencies, EUR base

- **Status:** Accepted
- **Date:** 2026-11-20

## Context

Two exchange rate sources arrive in bronze:

- **ECB** euro reference rates (Frankfurter, `bronze.frankfurter_rates`): about 30 currencies,
  units per 1 EUR, published on ECB business days.
- **CBAR** official rates (`bronze.cbar_rates`): about 37 currencies in AZN per `nominal` units
  (1 or 100), published on Azerbaijani business days.

Two of our city currencies, AZN and AED, are not in the ECB set. The silver layer needs one table
that answers "how many units of X per EUR on date D", with each value traceable to one source.
The two banks fix their rates at different times of day, so their implied cross rates differ
slightly (for example on 2026-11-19: ECB 1.0992 USD per EUR, CBAR implies 1.0963).

## Decision

Build `silver.exchange_rates` (dbt, contract enforced) with **EUR as the base**:

- **EUR = 1** on every ECB date. The ECB feed's own EUR row is ignored in favour of this rule.
- **ECB is the primary source for every currency it publishes**, decided per currency, not per
  day: if ECB skips a date (its holiday) for a currency it normally publishes, there is no CBAR
  fallback for that currency on that date. A currency never mixes sources.
- **CBAR fills only currencies ECB never publishes** (currently AED, AZN, BYN, GEL, KGS, KWD, KZT,
  MDL, PKR, QAR, RSD, RUB, SAR, TMT, UAH, UZS), as cross rates through AZN from CBAR rows of the
  **same rate_date** only:
  `units_per_eur(X) = (AZN per 1 EUR) / (AZN per 1 X)`, and `units_per_eur(AZN) = AZN per 1 EUR`,
  where `AZN per 1 X = rate_azn / nominal`.
- Weekend and holiday repeats are collapsed in staging to one row per actual `rate_date` and
  currency per source (latest load wins). Each row records its `source` (`ecb` or `cbar`).
- Arithmetic: divisions use `decimal(20, 10)` inputs (Trino then returns `decimal(38, 18)`;
  `decimal(38, 18)` inputs would round to 6 decimals), stored as `decimal(24, 10)`.

## Alternatives considered

- **CBAR as the primary source (AZN base):** natural for Azerbaijan, but the ECB set is the
  widely used reference for European and global prices, and it is the only source for EUR-zone
  analysis without cross-rate noise.
- **Per-day fallback (use CBAR when ECB is missing that day):** fills ECB holidays, but a currency
  would switch sources from day to day and show small jumps from the different fixing times.
- **Averaging both sources:** hides where a number came from and is not an official rate.
- **USD base:** needs a cross rate for every ECB currency; EUR is the ECB's own base.

## Consequences

- One table answers conversions: `amount * units_per_eur(B) / units_per_eur(A)` on a date.
- Conversions between an ECB currency and a CBAR currency mix two fixings, so they can differ
  from either bank's own cross rate by a few tenths of a percent (100 USD -> 169.55 AZN on
  2026-11-19, while CBAR's own rate gives 170.00). Tests guard the AZN/USD peg band (1.69-1.71).
- ECB and CBAR publish on different days; consumers that need both use the latest date with both
  sources (as the city-currency test does).
- Adding a city whose currency is in neither source fails the city-currency test.
