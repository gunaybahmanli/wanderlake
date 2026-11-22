import Link from "next/link";

import { SITE_NAME } from "@/lib/site";

import { NavLinks } from "./NavLinks";

export function SiteHeader() {
  return (
    <header className="site-header">
      <div className="container header-inner">
        <Link href="/" className="brand">
          {SITE_NAME}
        </Link>
        <NavLinks />
      </div>
    </header>
  );
}
