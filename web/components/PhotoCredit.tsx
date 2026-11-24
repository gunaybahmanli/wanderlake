import type { CityImage } from "@/lib/data/types";

// The attribution every Commons image needs wherever it is shown (ADR-0011): the author, the
// licence (linked to its text when there is one), and "via Wikimedia Commons" linked to the
// file page, which has the full details.
export function PhotoCredit({ image, className }: { image: CityImage; className?: string }) {
  return (
    <p className={`photo-credit${className ? ` ${className}` : ""}`}>
      Photo: {image.artist},{" "}
      {image.license_url ? (
        <a href={image.license_url} rel="license noopener">
          {image.license_short_name}
        </a>
      ) : (
        image.license_short_name
      )}
      , via <a href={image.file_page_url}>Wikimedia Commons</a>
    </p>
  );
}
