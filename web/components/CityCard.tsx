import Link from "next/link";

import { formatShare, formatTemp } from "@/lib/data/helpers";
import type { CityImage, CityOverview } from "@/lib/data/types";

import { CityCover } from "./CityCover";
import { Flag } from "./Flag";

export function CityCard({
  city,
  image,
  pleasantShare,
}: {
  city: CityOverview;
  image: CityImage | null;
  pleasantShare?: number;
}) {
  return (
    <article className="card city-card">
      <CityCover name={city.name} image={image} />
      <div className="card-body">
        <h3 className="card-title">
          <Link href={`/cities/${city.city_id}`} prefetch={true} className="card-link">
            {city.name}
          </Link>
        </h3>
        <p className="card-country">
          <Flag src={city.flag_png_url} country={city.country_name} />
          <span>{city.country_name}</span>
        </p>
        <dl className="card-facts">
          <div>
            <dt>Today</dt>
            <dd>
              {city.today_weather_description ?? "No forecast"}
              {city.today_temp_max_c !== null && (
                <>
                  , {formatTemp(city.today_temp_max_c)} / {formatTemp(city.today_temp_min_c)}
                </>
              )}
            </dd>
          </div>
          <div>
            <dt>Best months</dt>
            <dd>{city.best_months?.join(", ") || "Not enough data"}</dd>
          </div>
          <div>
            <dt>Currency</dt>
            <dd>
              {city.currency}
              {city.primary_currency_symbol ? ` (${city.primary_currency_symbol})` : ""}
            </dd>
          </div>
          {pleasantShare !== undefined && (
            <div>
              <dt>Pleasant days</dt>
              <dd>{formatShare(pleasantShare)}</dd>
            </div>
          )}
        </dl>
      </div>
    </article>
  );
}
