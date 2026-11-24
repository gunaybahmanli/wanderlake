// next/image loader for the city cover photos (configured as `images.loaderFile`).
//
// The pipeline stores each photo's Commons thumbnail on upload.wikimedia.org (1280 px wide:
// .../thumb/x/xy/<file>/1280px-<file>). Instead of Next.js fetching and resizing it on the
// server, the browser loads Wikimedia's own thumbnail of the right width directly from its
// CDN, the way Wikipedia serves images. Why (ADR-0011): the server-side optimiser fetches as
// "node", which Wikimedia's User-Agent policy does not allow, and was answered with 429.
//
// Wikimedia only serves standard thumbnail widths quickly and reliably (others may be refused),
// so each requested width is rounded up to one of them, never above the stored 1280 px.
// next.config.ts uses the same widths for the srcset, so the rounding is normally exact.

export const COMMONS_THUMB_WIDTHS = [330, 500, 960, 1280] as const;

const ALLOWED_PREFIX = "https://upload.wikimedia.org/wikipedia/commons/";
const THUMB_SEGMENT = /\/(\d+)px-([^/]+)$/;

export function commonsThumbWidth(width: number): number {
  return COMMONS_THUMB_WIDTHS.find((w) => w >= width) ?? COMMONS_THUMB_WIDTHS.at(-1)!;
}

export default function wikimediaLoader({ src, width }: { src: string; width: number; quality?: number }): string {
  if (!src.startsWith(ALLOWED_PREFIX) || src.includes("?")) {
    // Only Commons media without a query string, as remotePatterns allowed before.
    throw new Error(`Image host not allowed: ${src}`);
  }
  const match = src.match(THUMB_SEGMENT);
  // Not a thumbnail (the original is smaller than 1280 px): use the stored file as it is.
  if (!src.includes("/thumb/") || !match) return src;
  const target = Math.min(commonsThumbWidth(width), Number(match[1]));
  return src.replace(THUMB_SEGMENT, `/${target}px-${match[2]}`);
}
