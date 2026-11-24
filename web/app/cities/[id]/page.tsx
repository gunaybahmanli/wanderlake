import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";

import { CityCover } from "@/components/CityCover";
import { ClimateChart } from "@/components/ClimateChart";
import { CurrencyConverter } from "@/components/CurrencyConverter";
import { DataUnavailable } from "@/components/DataUnavailable";
import { Flag } from "@/components/Flag";
import { getCityIds, getCityPage, loadOrUnavailable } from "@/lib/data";
import { toRateRows } from "@/lib/data/converter";
import {
  type CityPageData,
  alertStatusLabel,
  excerpt,
  formatDate,
  formatElevation,
  formatPopulation,
  formatTemp,
  govUkAdviceUrl,
} from "@/lib/data/helpers";

// Prerender every city at build time (incremental static regeneration: see lib/data/index.ts
// for the cache lifetime). An id that is not a city renders the not-found page.
// An unknown id must get a real 404 status. Two parts: the id is checked before anything
// streams (no <Suspense> around it), and ensureStatic "navigation" makes a path that was not
// prerendered wait for its complete render instead of sending the shared /cities/[id] shell
// first, which would already carry a 200. The data is cached, so this adds no wait.
export const ensureStatic = "navigation";
export async function generateStaticParams() {
  return (await getCityIds()).map((id) => ({ id }));
}

export async function generateMetadata(props: PageProps<"/cities/[id]">): Promise<Metadata> {
  const { id } = await props.params;
  const result = await loadOrUnavailable(() => getCityPage(id)).catch(() => null);
  const page = result?.ok ? result.data : null;
  if (!page) return { title: "City" };
  const { city } = page;
  return {
    title: `${city.name}, ${city.country_name}`,
    description: `${city.name} travel guide: today's weather, 7-day forecast, best months to visit (${city.best_months?.join(", ") ?? "n/a"}), currency and UK travel advice.`,
  };
}

function Facts({ page }: { page: CityPageData }) {
  const { city, details } = page;
  return (
    <section className="card facts" aria-labelledby="facts">
      <h2 id="facts">Facts</h2>
      <dl className="fact-list">
        <div>
          <dt>Country</dt>
          <dd className="with-flag">
            <Flag src={city.flag_png_url} country={city.country_name} />
            {city.country_name}
          </dd>
        </div>
        <div>
          <dt>Region</dt>
          <dd>
            {city.region}
            {details?.subregion ? ` (${details.subregion})` : ""}
          </dd>
        </div>
        <div>
          <dt>Population</dt>
          <dd>{formatPopulation(city.geonames_population)} (GeoNames)</dd>
        </div>
        <div>
          <dt>Time zone</dt>
          <dd>{city.timezone}</dd>
        </div>
        <div>
          <dt>Languages</dt>
          <dd>{city.language_names?.join(", ") || "Unknown"}</dd>
        </div>
        <div>
          <dt>Currency</dt>
          <dd>
            {city.primary_currency_name} ({city.currency}
            {city.primary_currency_symbol ? `, ${city.primary_currency_symbol}` : ""})
          </dd>
        </div>
        <div>
          <dt>Elevation</dt>
          <dd>{formatElevation(city.elevation_m, details?.elevation_source ?? null)}</dd>
        </div>
      </dl>
    </section>
  );
}

function Today({ page }: { page: CityPageData }) {
  const { city } = page;
  return (
    <section className="card today" aria-labelledby="today">
      <h2 id="today">Today</h2>
      {city.today_weather_description ? (
        <>
          <p className="today-summary">{city.today_weather_description}</p>
          <p className="today-temps">
            <span>
              High <strong>{formatTemp(city.today_temp_max_c)}</strong>
            </span>
            <span>
              Low <strong>{formatTemp(city.today_temp_min_c)}</strong>
            </span>
            {city.today_precipitation_probability !== null && (
              <span>
                Rain chance <strong>{Math.round(city.today_precipitation_probability)}%</strong>
              </span>
            )}
          </p>
          <p className="muted small">
            Forecast for {formatDate(city.forecast_date)}, local date in {city.timezone}.
          </p>
        </>
      ) : (
        <p>No forecast for today yet.</p>
      )}
    </section>
  );
}

function Forecast({ page }: { page: CityPageData }) {
  if (page.forecast.length === 0) {
    return (
      <section aria-labelledby="forecast">
        <h2 id="forecast">7-day forecast</h2>
        <p>No forecast available.</p>
      </section>
    );
  }
  return (
    <section aria-labelledby="forecast">
      <h2 id="forecast">7-day forecast</h2>
      <div className="table-wrap">
        <table className="forecast">
          <caption className="visually-hidden">
            Daily forecast for {page.city.name}: weather, temperatures and rain
          </caption>
          <thead>
            <tr>
              <th scope="col">Day</th>
              <th scope="col">Weather</th>
              <th scope="col">High</th>
              <th scope="col">Low</th>
              <th scope="col">Rain</th>
              <th scope="col">Wind</th>
            </tr>
          </thead>
          <tbody>
            {page.forecast.map((day) => (
              <tr key={day.date}>
                <th scope="row">
                  <time dateTime={day.date}>{day.label}</time>
                </th>
                <td data-label="Weather">{day.description}</td>
                <td data-label="High">{formatTemp(day.maxC)}</td>
                <td data-label="Low">{formatTemp(day.minC)}</td>
                <td data-label="Rain">
                  {day.precipitationMm ?? "-"} mm
                  {day.precipitationChance !== null && ` (${day.precipitationChance}%)`}
                </td>
                <td data-label="Wind">{day.windKmh ?? "-"} km/h</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="muted small">Forecast data by Open-Meteo.</p>
    </section>
  );
}

function Advice({ page }: { page: CityPageData }) {
  const { advice, city } = page;
  const url = govUkAdviceUrl(advice);
  const summary = excerpt(advice?.summary_text ?? null);
  return (
    <section className="card advice" aria-labelledby="advice">
      <h2 id="advice">Travel advice</h2>
      {!advice ? (
        <p>
          No UK travel advice is published for {city.country_name}.{" "}
          <a href={url}>Browse all GOV.UK foreign travel advice</a>.
        </p>
      ) : (
        <>
          {advice.has_alert ? (
            <div className="badge badge-alert">
              <strong>Travel alert</strong>
              <ul>
                {(advice.alert_statuses ?? []).map((s) => (
                  <li key={s}>{alertStatusLabel(s)}</li>
                ))}
              </ul>
            </div>
          ) : (
            <p className="badge badge-ok">No FCDO warnings against travel</p>
          )}
          {summary.text && (
            <div className="advice-text">
              {summary.text.split("\n\n").map((p, i) => (
                <p key={i}>{p}</p>
              ))}
              {summary.truncated && (
                <p>
                  <a href={url}>Read the full advice on GOV.UK</a>
                </p>
              )}
            </div>
          )}
          {advice.change_description && (
            <p className="small">
              <strong>Latest change:</strong> {advice.change_description}
            </p>
          )}
          <p className="small">
            Last updated by the FCDO on{" "}
            <time dateTime={advice.public_updated_at}>
              {formatDate(advice.public_updated_at)}
            </time>
            . <a href={url}>{advice.title ?? "Official advice"} on GOV.UK</a>
          </p>
        </>
      )}
      <p className="note small">
        This is a summary of the UK Foreign, Commonwealth &amp; Development Office advice for
        British travellers. Always check the official advice, and your own government&apos;s, before
        you travel.
      </p>
    </section>
  );
}

function BestMonths({ page }: { page: CityPageData }) {
  const months = page.city.best_months ?? [];
  return (
    <section aria-labelledby="best-months">
      <h2 id="best-months">Best months to visit</h2>
      {months.length === 0 ? (
        <p>Not enough climate data yet.</p>
      ) : (
        <>
          <ol className="pill-list">
            {months.map((m) => (
              <li key={m} className="pill">
                {m}
              </li>
            ))}
          </ol>
          <p className="muted small">
            The months with the most pleasant days (18 to 28&nbsp;&deg;C, less than 1&nbsp;mm of
            rain) over recent years.
          </p>
        </>
      )}
    </section>
  );
}

function Converter({ page }: { page: CityPageData }) {
  return (
    <section className="card" aria-labelledby="converter">
      <h2 id="converter">Currency converter</h2>
      <CurrencyConverter rates={toRateRows(page.rates)} cityCurrency={page.city.currency} />
      <p className="note small">
        Reference rates for information only (ECB, and CBAR for the Azerbaijani manat); banks and
        exchange offices apply their own rates and fees.
      </p>
    </section>
  );
}

function Climate({ page }: { page: CityPageData }) {
  return (
    <section aria-labelledby="climate">
      <h2 id="climate">Climate by month</h2>
      <ClimateChart
        cityId={page.city.city_id}
        cityName={page.city.name}
        climate={page.climate}
        bestMonths={page.city.best_months ?? []}
      />
    </section>
  );
}

function CityContent({ page }: { page: CityPageData }) {
  const { city } = page;

  return (
    <article className="city">
      <p className="breadcrumb">
        <Link href="/explore">All cities</Link>
      </p>
      <header className="city-header">
        <CityCover name={city.name} image={page.image} size="hero" preload />
        <h1>{city.name}</h1>
        <p className="lead with-flag">
          <Flag src={city.flag_png_url} country={city.country_name} />
          {city.country_name}, {city.region}
        </p>
      </header>

      <div className="city-grid">
        <Today page={page} />
        <Facts page={page} />
      </div>
      <Forecast page={page} />
      <BestMonths page={page} />
      <Climate page={page} />
      <Converter page={page} />
      <Advice page={page} />
    </article>
  );
}

export default async function CityPage(props: PageProps<"/cities/[id]">) {
  const { id } = await props.params;
  const result = await loadOrUnavailable(() => getCityPage(id));
  if (!result.ok) return <DataUnavailable />; // without data we cannot tell if the id exists
  if (!result.data) notFound();
  return <CityContent page={result.data} />;
}
