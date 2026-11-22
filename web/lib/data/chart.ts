// Pure helpers for the hand-built climate chart (components/ClimateChart.tsx): axis ranges,
// ticks, scales and the text summary. Unit tested in chart.test.ts.
import { MONTHS } from "./helpers";
import type { ClimateMonth } from "./types";

export type Axis = { min: number; max: number; ticks: number[] };

/** An axis from min to max rounded outwards to `step`, with a tick every `step`. */
export function niceAxis(values: number[], step: number, includeZero = false): Axis {
  const finite = values.filter((v) => Number.isFinite(v));
  let lo = finite.length ? Math.min(...finite) : 0;
  let hi = finite.length ? Math.max(...finite) : step;
  if (includeZero) lo = Math.min(lo, 0);
  lo = Math.floor(lo / step) * step;
  hi = Math.ceil(hi / step) * step;
  if (hi === lo) hi = lo + step;
  const ticks: number[] = [];
  for (let v = lo; v <= hi + 1e-9; v += step) ticks.push(Number(v.toFixed(6)));
  return { min: lo, max: hi, ticks };
}

/** Temperature axis: 5 degree steps, or 10 when the range is wide. */
export function temperatureAxis(months: ClimateMonth[]): Axis {
  const values = months.flatMap((m) => [m.avg_temp_max_c, m.avg_temp_min_c]).filter(isNum);
  const range = values.length ? Math.max(...values) - Math.min(...values) : 0;
  return niceAxis(values, range > 30 ? 10 : 5);
}

/** Rainy-day axis: from 0, steps of 2, 5 or 10 days so there are a handful of ticks. */
export function rainAxis(months: ClimateMonth[]): Axis {
  const values = months.map((m) => m.avg_rainy_days).filter(isNum);
  const max = values.length ? Math.max(...values) : 0;
  return niceAxis(values.length ? values : [0], max > 20 ? 10 : max > 8 ? 5 : 2, true);
}

/** Linear scale from an axis range to pixels (y grows downwards: pixelMax is the top). */
export function scale(axis: Axis, pixelMin: number, pixelMax: number) {
  return (value: number) =>
    pixelMin + ((value - axis.min) / (axis.max - axis.min)) * (pixelMax - pixelMin);
}

function isNum(v: number | null | undefined): v is number {
  return typeof v === "number" && Number.isFinite(v);
}

/** One row per calendar month (1-12), with nulls for months without data. */
export function twelveMonths(months: ClimateMonth[], cityId: string): ClimateMonth[] {
  const byMonth = new Map(months.map((m) => [m.month, m]));
  return MONTHS.map(
    (_, i) =>
      byMonth.get(i + 1) ?? {
        city_id: cityId,
        month: i + 1,
        years_covered: null,
        days_covered: null,
        avg_temp_max_c: null,
        avg_temp_min_c: null,
        avg_rainy_days: null,
        avg_sunshine_hours_per_day: null,
        pleasant_day_share: null,
      },
  );
}

/** A one-sentence summary for screen readers: warmest, coolest and wettest months. */
export function climateSummary(cityName: string, months: ClimateMonth[], best: string[]): string {
  const pick = (key: keyof ClimateMonth, cmp: (a: number, b: number) => boolean) => {
    let found: ClimateMonth | null = null;
    for (const m of months) {
      const v = m[key];
      if (typeof v !== "number") continue;
      if (!found || cmp(v, found[key] as number)) found = m;
    }
    return found;
  };
  const warm = pick("avg_temp_max_c", (a, b) => a > b);
  const cool = pick("avg_temp_min_c", (a, b) => a < b);
  const wet = pick("avg_rainy_days", (a, b) => a > b);
  const parts = [`Monthly climate in ${cityName}.`];
  if (warm) parts.push(`Warmest: ${MONTHS[warm.month - 1]}, ${Math.round(warm.avg_temp_max_c!)} °C by day.`);
  if (cool) parts.push(`Coolest: ${MONTHS[cool.month - 1]}, ${Math.round(cool.avg_temp_min_c!)} °C at night.`);
  if (wet) parts.push(`Most rainy days: ${MONTHS[wet.month - 1]}, about ${Math.round(wet.avg_rainy_days!)}.`);
  if (best.length) parts.push(`Best months: ${best.join(", ")}.`);
  return parts.join(" ");
}
