// Pure helpers that shape published rows for the pages. No I/O, so they run anywhere and are
// unit tested (helpers.test.ts).
import type {
  CityDetails,
  CityOverview,
  ClimateMonth,
  ExchangeRate,
  ForecastDay,
  SiteData,
  TravelAdvice,
} from "./types";

export const MONTHS = [
  "January", "February", "March", "April", "May", "June",
  "July", "August", "September", "October", "November", "December",
] as const;

/**
 * A month counts as good for travel when at least this share of its days is pleasant (one day
 * in five). The pleasant-day rule is strict (18-28 C and under 1 mm of rain), so with the
 * published data this keeps 1 to 8 of the 10 cities in every month.
 */
export const PLEASANT_SHARE_THRESHOLD = 0.2;

export function monthName(month: number): string {
  return MONTHS[month - 1] ?? "";
}

export type RankedCity = {
  city: CityOverview;
  climate: ClimateMonth;
};

/** Cities ranked by pleasant_day_share in the given month (1-12), best first; ties by name. */
export function rankCitiesByMonth(
  cities: CityOverview[],
  climate: ClimateMonth[],
  month: number,
): RankedCity[] {
  const byCity = new Map(
    climate.filter((c) => c.month === month).map((c) => [c.city_id, c]),
  );
  return cities
    .flatMap((city) => {
      const row = byCity.get(city.city_id);
      return row && row.pleasant_day_share !== null ? [{ city, climate: row }] : [];
    })
    .sort(
      (a, b) =>
        (b.climate.pleasant_day_share ?? 0) - (a.climate.pleasant_day_share ?? 0) ||
        a.city.name.localeCompare(b.city.name),
    );
}

// --- Explore filters (URL query) -----------------------------------------------------------

export type ExploreFilters = {
  region?: string;
  month?: number; // 1-12
  currency?: string;
};

type Query = Record<string, string | string[] | undefined>;

function first(value: string | string[] | undefined): string | undefined {
  const v = Array.isArray(value) ? value[0] : value;
  return v && v.trim() !== "" ? v.trim() : undefined;
}

/** Read filters from the query; unknown or malformed values are ignored. */
export function parseExploreFilters(query: Query, options: ExploreOptions): ExploreFilters {
  const filters: ExploreFilters = {};
  const region = first(query.region);
  if (region && options.regions.includes(region)) filters.region = region;
  const currency = first(query.currency)?.toUpperCase();
  if (currency && options.currencies.includes(currency)) filters.currency = currency;
  const month = Number(first(query.month));
  if (Number.isInteger(month) && month >= 1 && month <= 12) filters.month = month;
  return filters;
}

export type ExploreOptions = { regions: string[]; currencies: string[] };

export function exploreOptions(cities: CityOverview[]): ExploreOptions {
  const distinct = (values: (string | null)[]) =>
    [...new Set(values.filter((v): v is string => !!v))].sort();
  return {
    regions: distinct(cities.map((c) => c.region)),
    currencies: distinct(cities.map((c) => c.currency)),
  };
}

export type ExploreCard = {
  city: CityOverview;
  pleasantShare?: number; // set when a month is selected
};

/**
 * Apply the filters. With a month, only cities whose pleasant-day share that month reaches
 * PLEASANT_SHARE_THRESHOLD are kept, best first; otherwise cities are sorted by name.
 */
export function filterCities(
  cities: CityOverview[],
  climate: ClimateMonth[],
  filters: ExploreFilters,
): ExploreCard[] {
  const matching = cities.filter(
    (c) =>
      (!filters.region || c.region === filters.region) &&
      (!filters.currency || c.currency === filters.currency),
  );
  if (filters.month === undefined) {
    return [...matching]
      .sort((a, b) => a.name.localeCompare(b.name))
      .map((city) => ({ city }));
  }
  return rankCitiesByMonth(matching, climate, filters.month)
    .filter((r) => (r.climate.pleasant_day_share ?? 0) >= PLEASANT_SHARE_THRESHOLD)
    .map((r) => ({ city: r.city, pleasantShare: r.climate.pleasant_day_share ?? 0 }));
}

/** Query string for a filter set (only the set values), e.g. "?region=Asia&month=5". */
export function exploreQuery(filters: ExploreFilters): string {
  const params = new URLSearchParams();
  if (filters.region) params.set("region", filters.region);
  if (filters.month) params.set("month", String(filters.month));
  if (filters.currency) params.set("currency", filters.currency);
  const qs = params.toString();
  return qs ? `?${qs}` : "";
}

// --- City page -----------------------------------------------------------------------------

export type ForecastView = {
  date: string; // YYYY-MM-DD
  weekday: string; // "Mon"
  label: string; // "Mon 23 Nov"
  description: string;
  maxC: number | null;
  minC: number | null;
  precipitationMm: number | null;
  precipitationChance: number | null; // percent
  windKmh: number | null;
};

function utcDate(isoDate: string): Date {
  return new Date(`${isoDate.slice(0, 10)}T00:00:00Z`);
}

const roundOrNull = (v: number | null | undefined, digits = 0) =>
  v === null || v === undefined ? null : Number(v.toFixed(digits));

/** The city's forecast days in date order, shaped for the page. */
export function forecastForCity(forecasts: ForecastDay[], cityId: string): ForecastView[] {
  return forecasts
    .filter((f) => f.city_id === cityId)
    .sort((a, b) => a.forecast_date.localeCompare(b.forecast_date))
    .map((f) => {
      const date = utcDate(f.forecast_date);
      const weekday = date.toLocaleDateString("en-GB", { weekday: "short", timeZone: "UTC" });
      const dayMonth = date.toLocaleDateString("en-GB", {
        day: "numeric",
        month: "short",
        timeZone: "UTC",
      });
      return {
        date: f.forecast_date,
        weekday,
        label: `${weekday} ${dayMonth}`,
        description: f.weather_description ?? "No description",
        maxC: roundOrNull(f.temp_max_c),
        minC: roundOrNull(f.temp_min_c),
        precipitationMm: roundOrNull(f.precipitation_mm, 1),
        precipitationChance: roundOrNull(f.precipitation_probability),
        windKmh: roundOrNull(f.wind_max_kmh),
      };
    });
}

export function formatPopulation(value: number | null): string {
  return value === null ? "Unknown" : new Intl.NumberFormat("en-GB").format(value);
}

export function formatElevation(metres: number | null, source: string | null): string {
  if (metres === null) return "Unknown";
  const label =
    source === "elevation"
      ? "GeoNames elevation"
      : source === "dem"
        ? "digital elevation model"
        : (source ?? "unknown source");
  return `${new Intl.NumberFormat("en-GB").format(metres)} m (${label})`;
}

export function formatDate(iso: string | null): string {
  if (!iso) return "Unknown";
  return new Date(iso).toLocaleDateString("en-GB", {
    day: "numeric",
    month: "long",
    year: "numeric",
    timeZone: "UTC",
  });
}

export function formatDateTime(iso: string | null): string {
  if (!iso) return "Unknown";
  return `${new Date(iso).toLocaleString("en-GB", {
    day: "numeric",
    month: "long",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    timeZone: "UTC",
  })} UTC`;
}

export function formatTemp(value: number | null): string {
  return value === null ? "-" : `${Math.round(value)}°C`;
}

export function formatShare(share: number): string {
  return `${Math.round(share * 100)}%`;
}

const ALERT_STATUS_LABELS: Record<string, string> = {
  avoid_all_travel_to_whole_country: "Advises against all travel to the whole country",
  avoid_all_travel_to_parts: "Advises against all travel to parts of the country",
  avoid_all_but_essential_travel_to_whole_country:
    "Advises against all but essential travel to the whole country",
  avoid_all_but_essential_travel_to_parts:
    "Advises against all but essential travel to parts of the country",
};

/** FCDO alert status codes in plain words (unknown codes: underscores become spaces). */
export function alertStatusLabel(status: string): string {
  const label = ALERT_STATUS_LABELS[status];
  if (label) return label;
  const words = status.replaceAll("_", " ");
  return words.charAt(0).toUpperCase() + words.slice(1);
}

/** Leading paragraphs of a text, up to about maxChars, without cutting a paragraph. */
export function excerpt(text: string | null, maxChars = 700): { text: string; truncated: boolean } {
  if (!text) return { text: "", truncated: false };
  const paragraphs = text.split(/\n{2,}/).map((p) => p.trim()).filter(Boolean);
  const kept: string[] = [];
  let length = 0;
  for (const p of paragraphs) {
    if (kept.length > 0 && length + p.length > maxChars) break;
    kept.push(p);
    length += p.length;
  }
  // Do not end on a lead-in such as "advises against travel to:" whose list was cut off.
  while (kept.length > 1 && kept.length < paragraphs.length && kept[kept.length - 1].endsWith(":")) {
    kept.pop();
  }
  return { text: kept.join("\n\n"), truncated: kept.length < paragraphs.length };
}

export const GOV_UK_ADVICE_BASE = "https://www.gov.uk/foreign-travel-advice";

export function govUkAdviceUrl(advice: TravelAdvice | null): string {
  return advice?.slug ? `${GOV_UK_ADVICE_BASE}/${advice.slug}` : GOV_UK_ADVICE_BASE;
}

export type CityPageData = {
  city: CityOverview;
  details: CityDetails | null;
  forecast: ForecastView[];
  advice: TravelAdvice | null;
  climate: ClimateMonth[];
  rates: ExchangeRate[]; // latest conversion matrix, for the converter
};

/** Everything one city page shows, or null for an unknown city id. */
export function cityPageData(data: SiteData, cityId: string): CityPageData | null {
  const city = data.cities.find((c) => c.city_id === cityId);
  if (!city) return null;
  return {
    city,
    details: data.details.find((d) => d.city_id === cityId) ?? null,
    forecast: forecastForCity(data.forecasts, cityId),
    advice: data.advice.find((a) => a.country_code === city.country_code) ?? null,
    climate: data.climate
      .filter((c) => c.city_id === cityId)
      .sort((a, b) => a.month - b.month),
    rates: data.rates,
  };
}

/** Newest _built_at across the city rows: when the published data was last refreshed. */
export function lastUpdated(cities: CityOverview[]): string | null {
  const times = cities.map((c) => c._built_at).filter((t): t is string => !!t);
  return times.length ? times.reduce((a, b) => (Date.parse(a) >= Date.parse(b) ? a : b)) : null;
}
