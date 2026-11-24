# ADR-0008: Unified exchange rates: ECB primary, CBAR for missing currencies, EUR base

- **Status:** Accepted
- **Date:** 2026-11-20 (amended 2026-11-24: direct CBAR rates for AZN pairs)

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
- **AZN pairs in the conversion matrix use CBAR's direct rate** (`gold.fct_exchange_rate`,
  amended 2026-11-24). On each date, AZN takes its value from the latest CBAR bulletin; if that
  same bulletin lists the other currency X, then `X -> AZN = AZN per 1 X` and `AZN -> X` is its
  inverse, both sides dated by that bulletin and sourced `cbar`. AZN <-> USD is therefore always
  the official 1.7000. Pairs without AZN, and AZN pairs whose currency CBAR does not publish
  (currently THB), stay cross rates through the EUR base. Requiring the same bulletin means a
  currency CBAR stops publishing falls back to the cross rate instead of a stale direct rate.
  `silver.exchange_rates` is unchanged.
- Arithmetic: divisions use `decimal(20, 10)` inputs (Trino then returns `decimal(38, 18)`;
  `decimal(38, 18)` inputs would round to 6 decimals), stored as `decimal(24, 10)`.

## Alternatives considered

- **Cross rates for every pair, AZN included** (the original decision): one rule, but the
  site's AZN/USD shows 1.68-1.72 instead of the peg on volatile days, which is wrong for the
  pair that matters most here. Replaced for AZN pairs by the direct-rate amendment.
- **CBAR direct rates in silver** (switching the source of USD, EUR and others to CBAR):
  would make every non-AZN pair mix two banks' fixings. Doing it in the gold matrix keeps ECB
  for pairs without AZN.

- **CBAR as the primary source (AZN base):** natural for Azerbaijan, but the ECB set is the
  widely used reference for European and global prices, and it is the only source for EUR-zone
  analysis without cross-rate noise.
- **Per-day fallback (use CBAR when ECB is missing that day):** fills ECB holidays, but a currency
  would switch sources from day to day and show small jumps from the different fixing times.
- **Averaging both sources:** hides where a number came from and is not an official rate.
- **USD base:** needs a cross rate for every ECB currency; EUR is the ECB's own base.

## Consequences

- One table answers conversions: `amount * units_per_eur(B) / units_per_eur(A)` on a date.
- Cross rates between an ECB currency and a CBAR currency mix two fixings, so they can differ
  from either bank's own rate by a few tenths of a percent. Before the amendment this applied to
  AZN/USD too: 100 USD -> 171.27 AZN on 2026-03-02 (ECB USD at 14:15 CET against CBAR's morning
  AZN per EUR), while CBAR's official rate gives 170.00; the first prod run with 400 days of
  history found such gaps up to 0.75% on volatile EUR/USD days. With direct CBAR rates for AZN
  pairs, 100 USD -> AZN is 170.00 on every date, and the residual mixing remains only in pairs
  without AZN and in AZN <-> THB.
- Tests: the AZN/USD rate in gold must be direct from CBAR and within 1.6999-1.7001
  (`assert_gold_azn_usd_official`); the AZN/USD cross rate in silver, which the remaining cross
  rates are built from, keeps a sanity band of 1.68-1.72 (`assert_silver_azn_usd_cross_in_band`;
  first 1.69-1.71, widened after the 400-day prod run).
- ECB and CBAR publish on different days; consumers that need both use the latest date with both
  sources (as the city-currency test does).
- Adding a city whose currency is in neither source fails the city-currency test.
