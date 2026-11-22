import "server-only";

import { createClient } from "@supabase/supabase-js";

import type { SiteData } from "./types";

// Reads the published tables from Supabase's REST API (schema "api", ADR-0010) with the anon
// key, which row-level security limits to SELECT. The key stays on the server: it is read from
// SUPABASE_URL / SUPABASE_ANON_KEY (not NEXT_PUBLIC_*), and this module cannot be imported
// into client components ("server-only").

export class DataUnavailableError extends Error {
  constructor(message: string, options?: { cause?: unknown }) {
    super(message, options);
    this.name = "DataUnavailableError";
  }
}

function client() {
  const url = process.env.SUPABASE_URL;
  const key = process.env.SUPABASE_ANON_KEY;
  if (!url || !key) {
    throw new DataUnavailableError(
      "SUPABASE_URL and SUPABASE_ANON_KEY are not set: run 'make web-env', or set DATA_SOURCE=fixture.",
    );
  }
  return createClient(url, key, {
    db: { schema: "api" },
    auth: { persistSession: false, autoRefreshToken: false },
  });
}

async function select<T>(table: string, columns: string): Promise<T[]> {
  // Every table read here is small (at most a few hundred rows, PostgREST max_rows is 1000).
  const { data, error } = await client().from(table).select(columns);
  if (error) {
    throw new DataUnavailableError(`Supabase api.${table}: ${error.message}`, { cause: error });
  }
  return (data ?? []) as T[];
}

export async function loadFromSupabase(): Promise<SiteData> {
  const [cities, details, climate, forecasts, advice] = await Promise.all([
    select<SiteData["cities"][number]>("city_overview", "*"),
    select<SiteData["details"][number]>(
      "dim_city",
      "city_id,subregion,elevation_source,rate_source",
    ),
    select<SiteData["climate"][number]>(
      "agg_city_climate_monthly",
      "city_id,month,years_covered,days_covered,avg_temp_max_c,avg_temp_min_c,avg_rainy_days,avg_sunshine_hours_per_day,pleasant_day_share",
    ),
    select<SiteData["forecasts"][number]>(
      "weather_forecast_latest",
      "city_id,forecast_date,run_date,weather_code,weather_description,temp_max_c,temp_min_c,precipitation_mm,precipitation_probability,wind_max_kmh,sunshine_hours,uv_index_max",
    ),
    select<SiteData["advice"][number]>(
      "travel_advice_current",
      "country_code,slug,title,has_alert,alert_statuses,public_updated_at,change_description,summary_text,safety_text,entry_requirements_text,health_text",
    ),
  ]);
  if (cities.length === 0) {
    throw new DataUnavailableError("Supabase api.city_overview returned no rows.");
  }
  return { cities, details, climate, forecasts, advice };
}
