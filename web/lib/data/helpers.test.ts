import { describe, expect, it } from "vitest";

import { fixtureData } from "./fixtures";
import {
  alertStatusLabel,
  cityPageData,
  excerpt,
  exploreOptions,
  exploreQuery,
  filterCities,
  forecastForCity,
  formatElevation,
  formatPopulation,
  govUkAdviceUrl,
  imageFor,
  imagesByCity,
  lastUpdated,
  monthName,
  parseExploreFilters,
  rankCitiesByMonth,
} from "./helpers";
import type { ClimateMonth } from "./types";

const { cities, climate, forecasts, advice } = fixtureData;
const options = exploreOptions(cities);

describe("rankCitiesByMonth", () => {
  it("ranks cities by pleasant-day share, best first", () => {
    const ranked = rankCitiesByMonth(cities, climate, 5); // May: madrid .62, tokyo .60, baku .55
    expect(ranked.map((r) => r.city.city_id)).toEqual(["madrid", "tokyo", "baku"]);
  });

  it("breaks ties by city name and skips cities without data for the month", () => {
    const tie: ClimateMonth[] = [
      { ...climate[0], city_id: "tokyo", month: 1, pleasant_day_share: 0.5 },
      { ...climate[0], city_id: "baku", month: 1, pleasant_day_share: 0.5 },
    ];
    const ranked = rankCitiesByMonth(cities, tie, 1);
    expect(ranked.map((r) => r.city.city_id)).toEqual(["baku", "tokyo"]);
  });
});

describe("explore filters", () => {
  it("parses valid values from the query and ignores the rest", () => {
    expect(
      parseExploreFilters({ region: "Asia", month: "10", currency: "jpy" }, options),
    ).toEqual({ region: "Asia", month: 10, currency: "JPY" });
    expect(
      parseExploreFilters({ region: "Atlantis", month: "13", currency: "XXX" }, options),
    ).toEqual({});
    expect(parseExploreFilters({ month: ["5", "6"] }, options)).toEqual({ month: 5 });
  });

  it("filters by region and currency, sorted by name", () => {
    const asia = filterCities(cities, climate, { region: "Asia" });
    expect(asia.map((c) => c.city.city_id)).toEqual(["baku", "tokyo"]);
    const euro = filterCities(cities, climate, { currency: "EUR" });
    expect(euro.map((c) => c.city.city_id)).toEqual(["madrid"]);
  });

  it("keeps only cities with enough pleasant days in the month, best first", () => {
    // November: tokyo .45 and baku .24 reach the 20% threshold; madrid .12 does not.
    const november = filterCities(cities, climate, { month: 11 });
    expect(november.map((c) => [c.city.city_id, c.pleasantShare])).toEqual([
      ["tokyo", 0.45],
      ["baku", 0.24],
    ]);
  });

  it("builds a shareable query string", () => {
    expect(exploreQuery({ region: "Asia", month: 5 })).toBe("?region=Asia&month=5");
    expect(exploreQuery({})).toBe("");
  });

  it("offers the regions and currencies present in the data", () => {
    expect(options).toEqual({ regions: ["Asia", "Europe"], currencies: ["AZN", "EUR", "JPY"] });
  });
});

describe("forecastForCity", () => {
  it("returns the city's days in date order with rounded values and labels", () => {
    const days = forecastForCity([...forecasts].reverse(), "baku");
    expect(days).toHaveLength(7);
    expect(days.map((d) => d.date)).toEqual([...days.map((d) => d.date)].sort());
    expect(days[0]).toMatchObject({ date: "2026-11-22", weekday: "Sun", label: "Sun 22 Nov" });
    expect(Number.isInteger(days[0].maxC)).toBe(true);
  });

  it("keeps missing values as null", () => {
    const [day] = forecastForCity(
      [{ ...forecasts[0], temp_max_c: null, weather_description: null }],
      forecasts[0].city_id,
    );
    expect(day.maxC).toBeNull();
    expect(day.description).toBe("No description");
  });
});

describe("city page data", () => {
  it("joins the city with its details, forecast, advice and climate", () => {
    const page = cityPageData(fixtureData, "baku");
    expect(page?.details?.elevation_source).toBe("dem");
    expect(page?.forecast).toHaveLength(7);
    expect(page?.advice?.country_code).toBe("AZ");
    expect(page?.climate.map((c) => c.month)).toEqual([1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12]);
  });

  it("returns null for an unknown city", () => {
    expect(cityPageData(fixtureData, "atlantis")).toBeNull();
  });

  it("links to the country's GOV.UK page, or the index without a slug", () => {
    expect(govUkAdviceUrl(advice[0])).toBe("https://www.gov.uk/foreign-travel-advice/azerbaijan");
    expect(govUkAdviceUrl(null)).toBe("https://www.gov.uk/foreign-travel-advice");
  });
});

describe("formatting", () => {
  it("formats population, elevation with its source, and month names", () => {
    expect(formatPopulation(2351300)).toBe("2,351,300");
    expect(formatPopulation(null)).toBe("Unknown");
    expect(formatElevation(-23, "dem")).toBe("-23 m (digital elevation model)");
    expect(formatElevation(667, "elevation")).toBe("667 m (GeoNames elevation)");
    expect(monthName(1)).toBe("January");
  });

  it("describes FCDO alert statuses in plain words", () => {
    expect(alertStatusLabel("avoid_all_travel_to_parts")).toBe(
      "Advises against all travel to parts of the country",
    );
    expect(alertStatusLabel("some_new_status")).toBe("Some new status");
  });

  it("cuts long advice text at a paragraph boundary", () => {
    const text = ["a".repeat(400), "b".repeat(400), "c".repeat(10)].join("\n\n");
    expect(excerpt(text, 700)).toEqual({ text: "a".repeat(400), truncated: true });
    expect(excerpt("short", 700)).toEqual({ text: "short", truncated: false });
    expect(excerpt(null)).toEqual({ text: "", truncated: false });
    const lead = ["Intro.", "Avoid travel to:", "Region A ".repeat(100)].join("\n\n");
    expect(excerpt(lead, 100)).toEqual({ text: "Intro.", truncated: true });
  });

  it("finds the newest build time", () => {
    const rows = [
      { ...cities[0], _built_at: "2026-11-22T10:00:00+00:00" },
      { ...cities[1], _built_at: "2026-11-22T11:00:00+00:00" },
    ];
    expect(lastUpdated(rows)).toBe("2026-11-22T11:00:00+00:00");
    expect(lastUpdated([])).toBeNull();
  });
});

describe("fixture data", () => {
  it("is consistent: every city has 12 climate months, 7 forecast days and its advice", () => {
    for (const city of cities) {
      expect(climate.filter((c) => c.city_id === city.city_id)).toHaveLength(12);
      expect(forecasts.filter((f) => f.city_id === city.city_id)).toHaveLength(7);
      expect(advice.some((a) => a.country_code === city.country_code)).toBe(true);
    }
  });
});

describe("city images", () => {
  it("finds a city's cover image, or null for the placeholder", () => {
    expect(imageFor(fixtureData.images, "tokyo")?.license_short_name).toBe("CC BY-SA 3.0");
    expect(imageFor(fixtureData.images, "baku")).toBeNull();
    expect(cityPageData(fixtureData, "tokyo")?.image?.artist).toBe("Morio");
    expect(cityPageData(fixtureData, "madrid")?.image).toBeNull();
  });

  it("maps images by city and skips rows without a thumbnail", () => {
    const map = imagesByCity([...fixtureData.images, { ...fixtureData.images[0], city_id: "x", thumb_url: "" }]);
    expect(Object.keys(map)).toEqual(["tokyo"]);
  });
});
