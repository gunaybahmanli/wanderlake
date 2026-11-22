export function DataUnavailable({ retry }: { retry?: () => void }) {
  return (
    <section className="notice" role="status" aria-live="polite">
      <h2>Data temporarily unavailable</h2>
      <p>
        We could not load the latest travel data right now. Please try again in a few minutes.
      </p>
      {retry ? (
        <button type="button" className="button" onClick={retry}>
          Try again
        </button>
      ) : (
        <p>
          <a href="">Reload this page</a>
        </p>
      )}
    </section>
  );
}
