// Country flag from the published data (flag_png_url, REST Countries). Plain <img>: the files
// are small, already sized PNGs on a CDN, so next/image optimisation would add nothing.
export function Flag({ src, country }: { src: string | null; country: string | null }) {
  if (!src) return null;
  return (
    // eslint-disable-next-line @next/next/no-img-element
    <img
      className="flag"
      src={src}
      alt={country ? `Flag of ${country}` : "Flag"}
      width={36}
      height={24}
      loading="lazy"
      decoding="async"
    />
  );
}
