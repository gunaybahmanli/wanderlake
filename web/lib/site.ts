// Site-wide constants and build information (no data access).
export const SITE_NAME = "Wanderlake";
export const SITE_URL = "https://wanderlake.vercel.app";
export const REPO_URL = "https://github.com/gunaybahmanli/wanderlake";

/** The deployed commit, read at build time: Vercel builds once per commit. */
export function deployedCommit(): { label: string; href?: string } {
  const sha = process.env.VERCEL_GIT_COMMIT_SHA;
  if (!sha) return { label: "local" };
  return { label: sha.slice(0, 7), href: `${REPO_URL}/commit/${sha}` };
}
