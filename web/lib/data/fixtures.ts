// Fixture data (DATA_SOURCE=fixture): a small, realistic sample in the shape of the published
// tables, so builds, CI and tests run without Supabase. Values are illustrative, not current.
import type {
  CityDetails,
  CityOverview,
  CityImage,
  ClimateMonth,
  ExchangeRate,
  ForecastDay,
  SiteData,
  TravelAdvice,
} from "./types";

const BUILT_AT = "2026-11-22T16:40:00+00:00";
const RUN_DATE = "2026-11-22";

const cities: CityOverview[] = [
  {
    city_id: "baku", name: "Baku", country_code: "AZ", country_name: "Azerbaijan",
    region: "Asia", flag_png_url: "https://flags.restcountries.com/v5/w640/az.png",
    timezone: "Asia/Baku", latitude: 40.3667, longitude: 49.8352, currency: "AZN",
    primary_currency_name: "Azerbaijani manat", primary_currency_symbol: "₼",
    language_names: ["Azerbaijani"], geonames_population: 2351300, elevation_m: -23,
    rate_to_eur: 0.535733419051, rate_to_usd: 0.588235294118, rate_to_azn: 1,
    forecast_date: RUN_DATE, today_weather_description: "Slight rain showers",
    today_temp_max_c: 15.1, today_temp_min_c: 11, today_precipitation_probability: 58,
    has_travel_alert: true, advice_updated_at: "2026-10-23T12:44:51+00:00",
    best_months: ["October", "May", "September"], _built_at: BUILT_AT,
  },
  {
    city_id: "madrid", name: "Madrid", country_code: "ES", country_name: "Spain",
    region: "Europe", flag_png_url: "https://flags.restcountries.com/v5/w640/es.png",
    timezone: "Europe/Madrid", latitude: 40.4165, longitude: -3.7026, currency: "EUR",
    primary_currency_name: "Euro", primary_currency_symbol: "€",
    language_names: ["Spanish"], geonames_population: 3255944, elevation_m: 667,
    rate_to_eur: 1, rate_to_usd: 1.098, rate_to_azn: 1.8666,
    forecast_date: RUN_DATE, today_weather_description: "Mainly clear",
    today_temp_max_c: 14.2, today_temp_min_c: 4.8, today_precipitation_probability: 5,
    has_travel_alert: false, advice_updated_at: "2026-09-30T10:00:00+00:00",
    best_months: ["May", "October", "June"], _built_at: BUILT_AT,
  },
  {
    city_id: "tokyo", name: "Tokyo", country_code: "JP", country_name: "Japan",
    region: "Asia", flag_png_url: "https://flags.restcountries.com/v5/w640/jp.png",
    timezone: "Asia/Tokyo", latitude: 35.6895, longitude: 139.6917, currency: "JPY",
    primary_currency_name: "Japanese yen", primary_currency_symbol: "¥",
    language_names: ["Japanese"], geonames_population: 8336599, elevation_m: 44,
    rate_to_eur: 0.0061, rate_to_usd: 0.0067, rate_to_azn: 0.0114,
    forecast_date: RUN_DATE, today_weather_description: "Overcast",
    today_temp_max_c: 16.4, today_temp_min_c: 9.1, today_precipitation_probability: 20,
    has_travel_alert: false, advice_updated_at: "2026-11-02T08:15:00+00:00",
    best_months: ["May", "October", "November"], _built_at: BUILT_AT,
  },
];

const details: CityDetails[] = [
  { city_id: "baku", subregion: "Western Asia", elevation_source: "dem", rate_source: "cbar" },
  { city_id: "madrid", subregion: "Southern Europe", elevation_source: "elevation", rate_source: "ecb" },
  { city_id: "tokyo", subregion: "Eastern Asia", elevation_source: "dem", rate_source: "ecb" },
];

// Pleasant-day share per month (January first) for each city.
const PLEASANT: Record<string, number[]> = {
  baku: [0, 0, 0.02, 0.18, 0.55, 0.38, 0.12, 0.1, 0.45, 0.6, 0.24, 0.01],
  madrid: [0.01, 0.05, 0.2, 0.35, 0.62, 0.48, 0.15, 0.12, 0.42, 0.58, 0.12, 0.01],
  tokyo: [0, 0.01, 0.1, 0.38, 0.6, 0.35, 0.08, 0.05, 0.3, 0.55, 0.45, 0.04],
};
const MAX_TEMP: Record<string, number[]> = {
  baku: [7, 8, 11, 17, 23, 28, 31, 31, 26, 20, 14, 9],
  madrid: [10, 12, 16, 18, 23, 29, 33, 32, 27, 20, 14, 10],
  tokyo: [10, 11, 14, 19, 23, 26, 30, 31, 27, 22, 17, 12],
};

const climate: ClimateMonth[] = Object.entries(PLEASANT).flatMap(([cityId, shares]) =>
  shares.map((share, i) => ({
    city_id: cityId,
    month: i + 1,
    years_covered: 4,
    days_covered: 120,
    avg_temp_max_c: MAX_TEMP[cityId][i],
    avg_temp_min_c: MAX_TEMP[cityId][i] - 8,
    avg_rainy_days: 5,
    avg_sunshine_hours_per_day: 6,
    pleasant_day_share: share,
  })),
);

const DESCRIPTIONS = ["Overcast", "Mainly clear", "Slight rain", "Partly cloudy", "Clear sky"];

const forecasts: ForecastDay[] = cities.flatMap((city, c) =>
  Array.from({ length: 7 }, (_, d) => {
    const date = new Date(Date.UTC(2026, 10, 22 + d)).toISOString().slice(0, 10);
    const max = (city.today_temp_max_c ?? 15) + ((d + c) % 3) - 1;
    return {
      city_id: city.city_id,
      forecast_date: date,
      run_date: RUN_DATE,
      weather_code: 3,
      weather_description: DESCRIPTIONS[(d + c) % DESCRIPTIONS.length],
      temp_max_c: max,
      temp_min_c: max - 7,
      precipitation_mm: (d + c) % 4 === 2 ? 2.4 : 0,
      precipitation_probability: ((d + c) % 4) * 15,
      wind_max_kmh: 12 + d,
      sunshine_hours: 5,
      uv_index_max: 2,
    };
  }),
);

const advice: TravelAdvice[] = [
  {
    country_code: "AZ", slug: "azerbaijan", title: "Azerbaijan travel advice", has_alert: true,
    alert_statuses: ["avoid_all_travel_to_parts", "avoid_all_but_essential_travel_to_parts"],
    public_updated_at: "2026-10-23T12:44:51+00:00",
    change_description: "Sample: updated information on the border areas.",
    summary_text:
      "Sample text. The Foreign, Commonwealth & Development Office advises against all travel to some areas near the border.\n\nRead the official advice before you travel.",
    safety_text: null, entry_requirements_text: null, health_text: null,
  },
  {
    country_code: "ES", slug: "spain", title: "Spain travel advice", has_alert: false,
    alert_statuses: [], public_updated_at: "2026-09-30T10:00:00+00:00",
    change_description: "Sample: updated entry requirements.",
    summary_text: "Sample text. No travel can be guaranteed safe. Read all the advice in this guide.",
    safety_text: null, entry_requirements_text: null, health_text: null,
  },
  {
    country_code: "JP", slug: "japan", title: "Japan travel advice", has_alert: false,
    alert_statuses: [], public_updated_at: "2026-11-02T08:15:00+00:00",
    change_description: "Sample: updated information on natural disasters.",
    summary_text: "Sample text. No travel can be guaranteed safe. Read all the advice in this guide.",
    safety_text: null, entry_requirements_text: null, health_text: null,
  },
];

// Conversion matrix for the fixture currencies, built the way gold builds it: through the euro
// (EUR per 1 unit of each currency), so every pair and its inverse are consistent.
const EUR_PER_UNIT: Record<string, number> = {
  AZN: 0.535733419051, // CBAR: 1.7 AZN per USD
  EUR: 1,
  JPY: 0.0061,
  USD: 0.910746812386, // ECB: 1.098 USD per EUR
};
const RATE_SOURCE: Record<string, string> = { AZN: "cbar", EUR: "ecb", JPY: "ecb", USD: "ecb" };
// Pairs with AZN and one of these are direct CBAR rates (ADR-0008): both sides "cbar". JPY stays a
// cross rate here (as THB does in production) so the fixtures also cover a mixed-source pair.
const CBAR_DIRECT = new Set(["EUR", "USD"]);
const direct = (a: string, b: string) =>
  (a === "AZN" && CBAR_DIRECT.has(b)) || (b === "AZN" && CBAR_DIRECT.has(a));

const rates: ExchangeRate[] = Object.keys(EUR_PER_UNIT).flatMap((from) =>
  Object.keys(EUR_PER_UNIT)
    .filter((to) => to !== from)
    .map((to) => ({
      date: RUN_DATE,
      from_currency: from,
      to_currency: to,
      rate: EUR_PER_UNIT[from] / EUR_PER_UNIT[to],
      from_rate_date: from === "EUR" && !direct(from, to) ? RUN_DATE : "2026-11-20",
      to_rate_date: to === "EUR" && !direct(from, to) ? RUN_DATE : "2026-11-20",
      source_from: direct(from, to) ? "cbar" : RATE_SOURCE[from],
      source_to: direct(from, to) ? "cbar" : RATE_SOURCE[to],
    })),
);

// Cover images: Tokyo has one (real Commons metadata), Baku and Madrid have none, so the
// placeholder path stays covered by the tests.
const images: CityImage[] = [
  {
    city_id: "tokyo",
    file_name: "Skyscrapers of Shinjuku 2009 January (revised).jpg",
    file_page_url:
      "https://commons.wikimedia.org/wiki/File:Skyscrapers_of_Shinjuku_2009_January_(revised).jpg",
    thumb_url:
      "https://upload.wikimedia.org/wikipedia/commons/thumb/d/dc/Skyscrapers_of_Shinjuku_2009_January_%28revised%29.jpg/1280px-Skyscrapers_of_Shinjuku_2009_January_%28revised%29.jpg",
    thumb_width: 1280,
    thumb_height: 699,
    artist: "Morio",
    license_short_name: "CC BY-SA 3.0",
    license_family: "cc-by-sa",
    license_url: "https://creativecommons.org/licenses/by-sa/3.0",
  },
];

export const fixtureData: SiteData = {
  cities,
  details,
  climate,
  forecasts,
  advice,
  rates,
  images,
  publishedAt: "2026-11-22T17:48:00+00:00",
};
