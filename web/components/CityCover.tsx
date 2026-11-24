import Image from "next/image";

import type { CityImage } from "@/lib/data/types";

import { CityImage as Placeholder } from "./CityImage";
import { PhotoCredit } from "./PhotoCredit";

// A city's cover photo with its credit, or the neutral placeholder when there is no image.
// next/image picks a Wikimedia thumbnail of the right width for each screen (custom loader,
// lib/images/wikimedia-loader.ts); the stored width and height reserve the space, so the
// layout does not shift while it loads.
export function CityCover({
  name,
  image,
  size = "card",
  preload = false,
}: {
  name: string;
  image: CityImage | null;
  size?: "card" | "hero";
  preload?: boolean; // the hero image: load it early (Next 16 replaced `priority` with this)
}) {
  if (!image) return <Placeholder name={name} size={size} />;
  return (
    <figure className={`city-cover city-cover-${size}`}>
      <Image
        src={image.thumb_url}
        alt={`Photo of ${name}`}
        width={image.thumb_width}
        height={image.thumb_height}
        sizes={size === "hero" ? "(max-width: 68rem) 100vw, 68rem" : "(max-width: 40rem) 100vw, 22rem"}
        preload={preload}
      />
      <figcaption>
        <PhotoCredit image={image} />
      </figcaption>
    </figure>
  );
}
