import Link from "next/link";

export default function NotFound() {
  return (
    <section className="notice">
      <h1>Page not found</h1>
      <p>
        We do not have a page here. <Link href="/explore">Explore all cities</Link> or go back to
        the <Link href="/">home page</Link>.
      </p>
    </section>
  );
}
