import "server-only";

import { cacheLife } from "next/cache";

import { fixtureData } from "./fixtures";
import {
  cityPageData,
  exploreOptions,
  imagesByCity,
  lastUpdated,
  rankCitiesByMonth,
} from "./helpers";
import { DataUnavailableError, loadFromSupabase } from "./supabase";
import type { SiteData } from "./types";

// Data access for the pages: the only module pages import data from.
//
// Rendering choice (incremental static regeneration): the published data changes once a day
// (publish_cloud after wanderlake_transform), and every visitor sees the same pages. So all
// pages are prerendered at build time (the 10 city pages through generateStaticParams) and
// served from cache, and the data behind them is refreshed in the background at most once an
// hour. A visit never waits for Supabase or costs a database request, and a page is at most
// about an hour behind a publish. With cacheComponents (next.config.ts) this is expressed with
// "use cache" + cacheLife instead of the older `export const revalidate = 3600`. The "data"
// profile (next.config.ts): revalidate after 1 hour; if a refresh fails (Supabase down), the
// last good result keeps being served until it expires after 7 days.
//
// DATA_SOURCE=fixture uses the bundled sample (fixtures.ts): builds, CI and tests without
// Supabase. Anything else, or unset, reads Supabase.

export function dataSource(): "fixture" | "supabase" {
  return process.env.DATA_SOURCE === "fixture" ? "fixture" : "supabase";
}

/** All published rows the site uses (small tables), cached for the "data" lifetime. */
export async function getSiteData(): Promise<SiteData> {
  "use cache";
  cacheLife("data");
  return dataSource() === "fixture" ? fixtureData : loadFromSupabase();
}

export async function getCityIds(): Promise<string[]> {
  const { cities } = await getSiteData();
  return cities.map((c) => c.city_id).sort();
}

export async function getCityPage(cityId: string) {
  return cityPageData(await getSiteData(), cityId);
}

/** Cities ranked for the current month (UTC), computed inside the cache, so the month rolls
 * over with the hourly refresh. */
export async function getGoodThisMonth() {
  "use cache";
  cacheLife("data");
  const data = await getSiteData();
  const month = new Date().getUTCMonth() + 1;
  return {
    month,
    ranked: rankCitiesByMonth(data.cities, data.climate, month),
    images: imagesByCity(data.images),
  };
}

export async function getExploreData() {
  const data = await getSiteData();
  return {
    cities: data.cities,
    climate: data.climate,
    options: exploreOptions(data.cities),
    images: imagesByCity(data.images),
  };
}

export async function getLastUpdated(): Promise<string | null> {
  const { cities, publishedAt } = await getSiteData();
  return lastUpdated(cities, publishedAt);
}

/**
 * Run a data load for a page; on DataUnavailableError return { ok: false }, so the page renders the
 * "Data temporarily unavailable" message on the server instead of failing.
 *
 * This only happens when there is no usable cached result: a cached result whose refresh fails
 * keeps being served (stale-while-revalidate), so a Supabase outage does not replace good pages.
 * During `next build` the error is re-thrown on purpose: a build without data must fail, so
 * Vercel keeps the previous deployment instead of publishing "unavailable" pages.
 */
export type Loaded<T> = { ok: true; data: T } | { ok: false };

export async function loadOrUnavailable<T>(load: () => Promise<T>): Promise<Loaded<T>> {
  try {
    return { ok: true, data: await load() };
  } catch (error) {
    if (!(error instanceof DataUnavailableError)) throw error;
    if (process.env.NEXT_PHASE === "phase-production-build") throw error;
    console.error(error);
    return { ok: false };
  }
}
