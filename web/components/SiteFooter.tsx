import Link from "next/link";

import { getLastUpdated, loadOrUnavailable } from "@/lib/data";
import { formatDateTime } from "@/lib/data/helpers";
import { REPO_URL, deployedCommit } from "@/lib/site";

/** When the production pipeline last published (api.last_updated; cached like all site data). */
async function DataUpdated() {
  const result = await loadOrUnavailable(getLastUpdated);
  if (!result.ok || !result.data) return null;
  return (
    <>
      <span aria-hidden="true"> · </span>
      Data updated: <time dateTime={result.data}>{formatDateTime(result.data)}</time>
    </>
  );
}

export function SiteFooter() {
  const commit = deployedCommit();
  return (
    <footer className="site-footer">
      <div className="container footer-inner">
        <p>
          Data from REST Countries, GeoNames, Open-Meteo, ECB (via Frankfurter), CBAR and the UK
          FCDO; photos from Wikimedia Commons.{" "}
          <Link href="/about#sources">Sources and attribution</Link>.
        </p>
        <p>
          <a href={REPO_URL}>GitHub</a>
          <span aria-hidden="true"> · </span>
          Deployed commit:{" "}
          {commit.href ? (
            <a href={commit.href}>
              <code>{commit.label}</code>
            </a>
          ) : (
            <code>{commit.label}</code>
          )}
          <DataUpdated />
        </p>
      </div>
    </footer>
  );
}
