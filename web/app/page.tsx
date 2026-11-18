const REPO_URL = "https://github.com/gunaybahmanli/wanderlake";
const README_URL = `${REPO_URL}/blob/main/README.md`;

// Read at build time: Vercel builds once per commit, so this is the deployed commit.
function deployedCommit(): { label: string; href?: string } {
  const sha = process.env.VERCEL_GIT_COMMIT_SHA;
  if (!sha) return { label: "local" };
  return { label: sha.slice(0, 7), href: `${REPO_URL}/commit/${sha}` };
}

export default function Home() {
  const commit = deployedCommit();

  return (
    <div className="page">
      <main className="content">
        <h1>Wanderlake</h1>
        <p className="pitch">
          Global travel intelligence on a self-built open-source lakehouse, with AI
        </p>

        <p className="status">
          <span className="status-dot" aria-hidden="true" />
          <span>
            Status: <strong>Phase 0: foundation</strong>
          </span>
        </p>

        <nav className="links" aria-label="Project links">
          <a className="button primary" href={REPO_URL}>
            GitHub repository
          </a>
          <a className="button" href={README_URL}>
            Read the README
          </a>
        </nav>
      </main>

      <footer className="footer">
        Deployed commit:{" "}
        {commit.href ? (
          <a href={commit.href}>
            <code>{commit.label}</code>
          </a>
        ) : (
          <code>{commit.label}</code>
        )}
      </footer>
    </div>
  );
}
