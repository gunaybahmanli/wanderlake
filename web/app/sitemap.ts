import type { MetadataRoute } from "next";

import { getCityIds, loadOrUnavailable } from "@/lib/data";
import { SITE_URL } from "@/lib/site";

export default async function sitemap(): Promise<MetadataRoute.Sitemap> {
  const pages = ["", "/explore", "/about"].map((path) => ({
    url: `${SITE_URL}${path}`,
    changeFrequency: "daily" as const,
  }));
  const ids = await loadOrUnavailable(getCityIds);
  const cities = (ids.ok ? ids.data : []).map((id) => ({
    url: `${SITE_URL}/cities/${id}`,
    changeFrequency: "daily" as const,
  }));
  return [...pages, ...cities];
}
