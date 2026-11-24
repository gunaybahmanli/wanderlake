// Row types of the published tables in Supabase schema "api" (supabase/migrations), which
// mirror the dbt gold contracts (dbt/models/gold/_gold.yml). PostgREST returns numeric and
// double columns as JSON numbers, text[] as string arrays, and dates and timestamps as ISO
// strings. Only the tables and columns the website reads are listed.

/** api.city_overview: one row per configured city, ready for a page. */
export type CityOverview = {
  city_id: string;
  name: string;
  country_code: string;
  country_name: string | null;
  region: string | null;
  flag_png_url: string | null;
  timezone: string | null;
  latitude: number | null;
  longitude: number | null;
  currency: string | null;
  primary_currency_name: string | null;
  primary_currency_symbol: string | null;
  language_names: string[] | null;
  geonames_population: number | null;
  elevation_m: number | null;
  rate_to_eur: number;
  rate_to_usd: number;
  rate_to_azn: number;
  forecast_date: string | null; // today in the city's time zone (YYYY-MM-DD)
  today_weather_description: string | null;
  today_temp_max_c: number | null;
  today_temp_min_c: number | null;
  today_precipitation_probability: number | null;
  has_travel_alert: boolean | null;
  advice_updated_at: string | null;
  best_months: string[] | null; // month names, best first
  _built_at: string | null;
};

/** api.dim_city: the columns of the city dimension that city_overview does not carry. */
export type CityDetails = {
  city_id: string;
  subregion: string | null;
  elevation_source: string | null; // "elevation" (GeoNames) or "dem" (digital elevation model)
  rate_source: string | null; // "ecb" or "cbar"
};

/** api.agg_city_climate_monthly: one row per city and calendar month (1-12). */
export type ClimateMonth = {
  city_id: string;
  month: number;
  years_covered: number | null;
  days_covered: number | null;
  avg_temp_max_c: number | null;
  avg_temp_min_c: number | null;
  avg_rainy_days: number | null;
  avg_sunshine_hours_per_day: number | null;
  pleasant_day_share: number | null; // 0-1
};

/** api.weather_forecast_latest: 7-day forecast per city from its latest run. */
export type ForecastDay = {
  city_id: string;
  forecast_date: string;
  run_date: string;
  weather_code: number | null;
  weather_description: string | null;
  temp_max_c: number | null;
  temp_min_c: number | null;
  precipitation_mm: number | null;
  precipitation_probability: number | null;
  wind_max_kmh: number | null;
  sunshine_hours: number | null;
  uv_index_max: number | null;
};

/** api.travel_advice_current: the current UK FCDO advice per country. */
export type TravelAdvice = {
  country_code: string;
  slug: string | null;
  title: string | null;
  has_alert: boolean;
  alert_statuses: string[] | null;
  public_updated_at: string;
  change_description: string | null;
  summary_text: string | null;
  safety_text: string | null;
  entry_requirements_text: string | null;
  health_text: string | null;
};

/**
 * api.fct_exchange_rate, latest date only: the conversion matrix, one row per ordered pair of
 * different currencies. Convert with amount * rate (units of to_currency per from_currency).
 */
export type ExchangeRate = {
  date: string;
  from_currency: string;
  to_currency: string;
  rate: number;
  from_rate_date: string; // rate date actually used for from_currency (<= date)
  to_rate_date: string;
  source_from: string | null; // "ecb" or "cbar"
  source_to: string | null;
};

/**
 * api.city_images: one cover image per city from Wikimedia Commons (via Wikidata). Every display
 * must show the credit: artist, licence (linked) and "via Wikimedia Commons" (file page).
 */
export type CityImage = {
  city_id: string;
  file_name: string;
  file_page_url: string;
  thumb_url: string; // about 1280 px wide
  thumb_width: number;
  thumb_height: number;
  artist: string;
  license_short_name: string;
  license_family: string; // cc0 | public-domain | cc-by | cc-by-sa
  license_url: string | null;
};

/** Everything the website reads, loaded once per cache period (the tables are small). */
export type SiteData = {
  cities: CityOverview[];
  details: CityDetails[];
  climate: ClimateMonth[];
  forecasts: ForecastDay[];
  advice: TravelAdvice[];
  rates: ExchangeRate[]; // latest date only
  images: CityImage[];
};
