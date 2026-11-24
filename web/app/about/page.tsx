import type { Metadata } from "next";

import { getLastUpdated, loadOrUnavailable } from "@/lib/data";
import { formatDateTime } from "@/lib/data/helpers";
import { REPO_URL } from "@/lib/site";

export const metadata: Metadata = {
  title: "About and data sources",
  description:
    "How Wanderlake works, from open data to city pages, and the data sources and licences behind it.",
};

const SOURCES = [
  {
    name: "REST Countries",
    href: "https://restcountries.com",
    what: "Countries: names, regions, languages, currencies and flags.",
    licence: "REST Countries terms of use",
  },
  {
    name: "GeoNames",
    href: "https://www.geonames.org",
    what: "Cities: population, coordinates and elevation.",
    licence: "Creative Commons Attribution 4.0 (CC BY 4.0)",
    licenceHref: "https://creativecommons.org/licenses/by/4.0/",
  },
  {
    name: "Open-Meteo",
    href: "https://open-meteo.com",
    what: "Weather forecasts and historical weather, the basis of the climate figures.",
    licence: "Creative Commons Attribution 4.0 (CC BY 4.0)",
    licenceHref: "https://creativecommons.org/licenses/by/4.0/",
  },
  {
    name: "European Central Bank reference rates, via Frankfurter",
    href: "https://frankfurter.dev",
    what: "Daily euro exchange rates.",
    licence: "ECB reference rates are published for information purposes",
  },
  {
    name: "Central Bank of the Republic of Azerbaijan (CBAR)",
    href: "https://www.cbar.az",
    what: "Official daily rates of the Azerbaijani manat.",
    licence: "Official rates published by CBAR",
  },
  {
    name: "Wikimedia Commons",
    href: "https://commons.wikimedia.org",
    what: "City photos. Each photo has its own author and open licence (CC0, public domain, CC BY or CC BY-SA), credited next to it with a link to its file page. Photos load directly from Wikimedia Commons' servers, so your browser connects to Wikimedia to show them.",
    licence: "the licence shown with each photo",
  },
  {
    name: "Wikidata",
    href: "https://www.wikidata.org",
    what: "Which photo represents each city (the item's main image).",
    licence: "Creative Commons CC0",
    licenceHref: "https://creativecommons.org/publicdomain/zero/1.0/",
  },
  {
    name: "UK Foreign, Commonwealth & Development Office travel advice (GOV.UK)",
    href: "https://www.gov.uk/foreign-travel-advice",
    what: "Travel advice and alerts per country.",
    licence: "Open Government Licence v3.0",
    licenceHref: "https://www.nationalarchives.gov.uk/doc/open-government-licence/version/3/",
  },
];

async function LastUpdated() {
  const result = await loadOrUnavailable(getLastUpdated);
  return (
    <p>
      Data last updated:{" "}
      <strong>{result.ok ? formatDateTime(result.data) : "temporarily unavailable"}</strong>.
    </p>
  );
}

export default function AboutPage() {
  return (
    <>
      <h1>About Wanderlake</h1>
      <p className="lead">
        Wanderlake is a portfolio project: a data platform built from open-source parts that
        turns public data into simple city guides.
      </p>

      <section aria-labelledby="how">
        <h2 id="how">How it works</h2>
        <ol className="steps">
          <li>
            <strong>Collect.</strong> Every day, scheduled jobs download exchange rates, weather,
            travel advice and reference data from the sources below (city photos weekly), and
            keep an untouched copy of every download.
          </li>
          <li>
            <strong>Store.</strong> The data lands in a small lakehouse: open table files on
            object storage that a SQL engine can query.
          </li>
          <li>
            <strong>Model.</strong> Tested SQL models clean and combine it into tables shaped for
            this site: one row per city, a 7-day forecast, monthly climate, and the current
            travel advice.
          </li>
          <li>
            <strong>Publish.</strong> After each daily build, those tables are copied to a cloud
            database that this site can only read from, and saved as files for analysis.
          </li>
          <li>
            <strong>Show.</strong> These pages are generated from the published tables and
            refreshed at most every hour.
          </li>
        </ol>
        <LastUpdated />
        <p>
          The code, architecture and decisions are on <a href={REPO_URL}>GitHub</a>.
        </p>
      </section>

      <section aria-labelledby="sources-heading" id="sources">
        <h2 id="sources-heading">Data sources and attribution</h2>
        <ul className="sources">
          {SOURCES.map((s) => (
            <li key={s.name}>
              <a href={s.href}>{s.name}</a>: {s.what}{" "}
              <span className="muted">
                Licence:{" "}
                {s.licenceHref ? <a href={s.licenceHref}>{s.licence}</a> : s.licence}.
              </span>
            </li>
          ))}
        </ul>
        <p className="small">
          Contains public sector information licensed under the Open Government Licence v3.0.
          City photos are not covered by this site&apos;s code licence: reuse them under their own
          licence, with the credit shown.
          Travel advice is summarised; always check the official advice before you travel.
          Climate figures are averages over recent years, not forecasts.
        </p>
      </section>
    </>
  );
}
