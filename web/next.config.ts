import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  cacheComponents: true,
  partialPrefetching: true,
  // Cache lifetime of the published data (lib/data/index.ts): refresh in the background at most
  // once an hour; serve a cached result for up to 5 minutes without checking (client router);
  // keep serving the last good result for up to 7 days if refreshes fail.
  cacheLife: {
    data: { stale: 300, revalidate: 3600, expire: 7 * 24 * 3600 },
  },
  // City cover images (api.city_images) come from Wikimedia Commons. The browser loads
  // Wikimedia's own thumbnails directly (lib/images/wikimedia-loader.ts) instead of the
  // server-side optimiser, which Wikimedia answered with 429 (ADR-0011). The loader accepts
  // only upload.wikimedia.org/wikipedia/commons/ URLs without a query string. The srcset widths
  // are Wikimedia's standard thumbnail widths, so every candidate is a real Commons thumbnail.
  images: {
    loader: "custom",
    loaderFile: "./lib/images/wikimedia-loader.ts",
    imageSizes: [330],
    deviceSizes: [500, 960, 1280], // 500 here too: with "vw" in `sizes`, Next starts at deviceSizes[0]
  },
};

export default nextConfig;
