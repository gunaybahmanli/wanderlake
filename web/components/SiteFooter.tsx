import Link from "next/link";

import { REPO_URL, deployedCommit } from "@/lib/site";

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
        </p>
      </div>
    </footer>
  );
}
