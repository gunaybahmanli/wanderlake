import Link from "next/link";

import { CityCover } from "@/components/CityCover";
import { DataUnavailable } from "@/components/DataUnavailable";
import { Flag } from "@/components/Flag";
import { getGoodThisMonth, loadOrUnavailable } from "@/lib/data";
import { formatShare, formatTemp, monthName } from "@/lib/data/helpers";

async function GoodThisMonth() {
  const result = await loadOrUnavailable(getGoodThisMonth);
  if (!result.ok) return <DataUnavailable />;
  const { month, ranked, images } = result.data;
  const name = monthName(month);

  return (
    <section aria-labelledby="this-month">
      <h2 id="this-month">Good to visit in {name}</h2>
      <p className="muted">
        Ranked by the share of pleasant days in {name} over recent years: a maximum temperature
        between 18 and 28&nbsp;&deg;C and less than 1&nbsp;mm of rain.
      </p>
      {ranked.length === 0 ? (
        <p>No climate data for this month yet.</p>
      ) : (
        <ol className="ranking">
          {ranked.map(({ city, climate }, i) => (
            <li key={city.city_id} className="ranking-item">
              <span className="ranking-pos" aria-hidden="true">
                {i + 1}
              </span>
              <CityCover name={city.name} image={images[city.city_id] ?? null} />
              <div className="ranking-body">
                <h3>
                  <Link href={`/cities/${city.city_id}`} prefetch={true}>
                    {city.name}
                  </Link>
                </h3>
                <p className="card-country">
                  <Flag src={city.flag_png_url} country={city.country_name} />
                  <span>{city.country_name}</span>
                </p>
                <p>
                  <strong>{formatShare(climate.pleasant_day_share ?? 0)}</strong> pleasant days,
                  typically {formatTemp(climate.avg_temp_max_c)} by day and{" "}
                  {formatTemp(climate.avg_temp_min_c)} at night.
                </p>
              </div>
            </li>
          ))}
        </ol>
      )}
      <p>
        <Link href={`/explore?month=${month}`}>See the cities with good weather in {name}</Link>
      </p>
    </section>
  );
}

export default function Home() {
  return (
    <>
      <section className="hero">
        <h1>Plan city trips with open data</h1>
        <p className="lead">
          Wanderlake turns open data into simple city guides: today&apos;s weather, the 7-day
          forecast, the best months to visit, currencies and the official UK travel advice,
          refreshed every day.
        </p>
        <p>
          <Link className="button primary" href="/explore">
            Explore all cities
          </Link>
        </p>
      </section>
      <GoodThisMonth />
    </>
  );
}
