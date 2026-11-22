import type { Metadata } from "next";
import Link from "next/link";
import { Suspense } from "react";

import { CityCard } from "@/components/CityCard";
import { DataUnavailable } from "@/components/DataUnavailable";
import { getExploreData, loadOrUnavailable } from "@/lib/data";
import {
  MONTHS,
  PLEASANT_SHARE_THRESHOLD,
  filterCities,
  formatShare,
  monthName,
  parseExploreFilters,
} from "@/lib/data/helpers";

export const metadata: Metadata = {
  title: "Explore cities",
  description:
    "Every Wanderlake city with today's weather, best months and currency; filter by region, travel month and currency.",
};

// Filters live in the URL query (shareable links; the form works without JavaScript). The
// query is request data, so only the results below read it, inside <Suspense>: the heading
// and form stay in the prerendered shell, and the results use the hourly cached data.
async function Results({ searchParams }: Pick<PageProps<"/explore">, "searchParams">) {
  const result = await loadOrUnavailable(getExploreData);
  if (!result.ok) return <DataUnavailable />;
  const { cities, climate, options } = result.data;
  const filters = parseExploreFilters(await searchParams, options);
  const cards = filterCities(cities, climate, filters);
  const active = Object.keys(filters).length > 0;

  return (
    <>
      <FilterForm options={options} filters={filters} />
      <p className="muted" role="status">
        {cards.length} of {cities.length} cities
        {filters.month !== undefined &&
          ` with at least ${formatShare(PLEASANT_SHARE_THRESHOLD)} pleasant days in ${monthName(filters.month)}, best first`}
        .{" "}
        {active && <Link href="/explore">Clear filters</Link>}
      </p>
      {cards.length === 0 ? (
        <p>No city matches these filters. Try another month or region.</p>
      ) : (
        <ul className="card-grid">
          {cards.map((card) => (
            <li key={card.city.city_id}>
              <CityCard city={card.city} pleasantShare={card.pleasantShare} />
            </li>
          ))}
        </ul>
      )}
    </>
  );
}

function FilterForm({
  options,
  filters,
}: {
  options: { regions: string[]; currencies: string[] };
  filters: { region?: string; month?: number; currency?: string };
}) {
  return (
    <form className="filters" method="get" action="/explore" aria-label="Filter cities">
      <div className="field">
        <label htmlFor="region">Region</label>
        <select id="region" name="region" defaultValue={filters.region ?? ""}>
          <option value="">All regions</option>
          {options.regions.map((r) => (
            <option key={r} value={r}>
              {r}
            </option>
          ))}
        </select>
      </div>
      <div className="field">
        <label htmlFor="month">Travel month</label>
        <select id="month" name="month" defaultValue={filters.month ?? ""}>
          <option value="">Any month</option>
          {MONTHS.map((m, i) => (
            <option key={m} value={i + 1}>
              {m}
            </option>
          ))}
        </select>
      </div>
      <div className="field">
        <label htmlFor="currency">Currency</label>
        <select id="currency" name="currency" defaultValue={filters.currency ?? ""}>
          <option value="">All currencies</option>
          {options.currencies.map((c) => (
            <option key={c} value={c}>
              {c}
            </option>
          ))}
        </select>
      </div>
      <button type="submit" className="button primary">
        Apply filters
      </button>
    </form>
  );
}

export default function ExplorePage(props: PageProps<"/explore">) {
  return (
    <>
      <h1>Explore cities</h1>
      <p className="lead">
        Filter by region, currency, or the month you want to travel: a month keeps the cities
        where at least one day in five has pleasant weather, best first.
      </p>
      <Suspense fallback={<p className="muted">Loading cities...</p>}>
        <Results searchParams={props.searchParams} />
      </Suspense>
    </>
  );
}
