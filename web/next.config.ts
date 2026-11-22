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
};

export default nextConfig;
