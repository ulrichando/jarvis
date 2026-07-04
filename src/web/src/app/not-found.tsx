import Link from "next/link";

// Branded 404 for any unmatched route (typos, stale bookmarks, removed pages).
// Without this, Next serves its bare default 404 — the "page cannot be found"
// the user hit. App-router picks this up for every unmatched page path.
export default function NotFound() {
  const links = [
    { href: "/", label: "Home" },
    { href: "/chat", label: "Chat" },
    { href: "/code", label: "Code" },
    { href: "/settings", label: "Settings" },
  ];
  return (
    <div className="flex min-h-screen flex-col items-center justify-center gap-6 bg-background px-4 text-center">
      <div>
        <p className="text-[64px] font-semibold leading-none text-foreground/90">404</p>
        <h1 className="mt-3 text-[18px] font-medium text-foreground">This page can’t be found</h1>
        <p className="mt-1.5 text-[14px] text-muted-foreground">
          The link may be broken, or the page may have moved.
        </p>
      </div>
      <nav className="flex flex-wrap items-center justify-center gap-2">
        {links.map((l) => (
          <Link
            key={l.href}
            href={l.href}
            className="rounded-full border border-border px-4 py-1.5 text-[13px] text-foreground/85 transition-colors hover:bg-accent/40"
          >
            {l.label}
          </Link>
        ))}
      </nav>
    </div>
  );
}
