import { describe, expect, it } from "vitest";

import { climateSummary, niceAxis, rainAxis, scale, temperatureAxis, twelveMonths } from "./chart";
import { fixtureData } from "./fixtures";

const baku = fixtureData.climate.filter((c) => c.city_id === "baku");

describe("axes", () => {
  it("rounds outwards to the step and ticks every step", () => {
    expect(niceAxis([3, 31], 5)).toEqual({ min: 0, max: 35, ticks: [0, 5, 10, 15, 20, 25, 30, 35] });
    expect(niceAxis([-7, 12], 5).ticks).toEqual([-10, -5, 0, 5, 10, 15]);
  });

  it("starts rainy days at zero and keeps a non-empty range", () => {
    expect(niceAxis([4, 9], 5, true)).toMatchObject({ min: 0, max: 10 });
    expect(niceAxis([], 2, true)).toMatchObject({ min: 0, max: 2 });
  });

  it("covers every value of the city", () => {
    const t = temperatureAxis(baku);
    for (const m of baku) {
      expect(m.avg_temp_min_c!).toBeGreaterThanOrEqual(t.min);
      expect(m.avg_temp_max_c!).toBeLessThanOrEqual(t.max);
    }
    expect(rainAxis(baku).min).toBe(0);
  });

  it("maps values linearly to pixels", () => {
    const y = scale({ min: 0, max: 10, ticks: [] }, 200, 0);
    expect(y(0)).toBe(200);
    expect(y(10)).toBe(0);
    expect(y(5)).toBe(100);
  });
});

describe("months", () => {
  it("fills missing months with nulls, in calendar order", () => {
    const months = twelveMonths(baku.filter((m) => m.month !== 3), "baku");
    expect(months.map((m) => m.month)).toEqual([1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12]);
    expect(months[2].avg_temp_max_c).toBeNull();
  });

  it("summarises the warmest, coolest and wettest months", () => {
    const text = climateSummary("Baku", baku, ["October", "May"]);
    expect(text).toContain("Warmest: July, 31 °C by day.");
    expect(text).toContain("Coolest: January");
    expect(text).toContain("Best months: October, May.");
  });
});
