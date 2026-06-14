import { getWorkspaceByShareToken, listAllFiles } from "@/lib/workspace/storage";
import { FORMAT_FILE } from "@/lib/design/format";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

// Public, read-only share page. Reachable WITHOUT login (allowlisted in
// src/proxy.ts) because the whole point is to share with someone who isn't
// the owner. It renders the deployed site if there is one, otherwise — for a
// /design workspace that's a static artifact, never "deployed" — the design's
// entry HTML, served through the token-scoped asset route so relative assets
// resolve. It NEVER exposes a file browser, env vars, or secrets. Unknown /
// expired tokens get a generic "invalid" screen so the page can't be used to
// probe for valid workspace ids.

// Find the design's primary entry HTML at the workspace root: prefer the
// known format files (landing/prototype/slides/onepager/infographic.html),
// then index.html, then the first root-level .html. Skips questions.html
// (a clarify-mode artifact, not the design).
async function findDesignEntry(wsId: string): Promise<string | null> {
  let files: string[];
  try {
    files = await listAllFiles(wsId);
  } catch {
    return null;
  }
  const rootHtml = files.filter(
    (f) => !f.includes("/") && /\.html?$/i.test(f) && f !== "questions.html",
  );
  if (rootHtml.length === 0) return null;
  for (const name of [...Object.values(FORMAT_FILE), "index.html"]) {
    if (rootHtml.includes(name)) return name;
  }
  return rootHtml[0];
}

export default async function SharePage({
  params,
}: {
  params: Promise<{ token: string }>;
}) {
  const { token } = await params;
  const ws = await getWorkspaceByShareToken(token);

  if (!ws) {
    return (
      <div className="flex h-dvh flex-col items-center justify-center gap-2 bg-background px-6 text-center">
        <p className="text-sm font-medium text-foreground">
          This share link is invalid or has expired.
        </p>
        <p className="text-xs text-muted-foreground">
          Ask the project owner for a fresh link.
        </p>
      </div>
    );
  }

  const liveUrl = ws.deploy?.productionUrl ?? null;
  // Static designs are never "deployed" — fall back to their entry HTML,
  // served through the token-scoped asset route so relative assets resolve.
  const designEntry = liveUrl ? null : await findDesignEntry(ws.id);
  const iframeSrc =
    liveUrl ?? (designEntry ? `/share/${token}/asset/${designEntry}` : null);

  return (
    <div className="flex h-dvh flex-col bg-background">
      <header className="flex items-center justify-between border-b border-border/50 px-4 py-2">
        <span className="truncate text-[13px] font-medium text-foreground/90">
          {ws.name}
        </span>
        <span className="shrink-0 text-[11px] text-muted-foreground">
          shared via JARVIS
        </span>
      </header>
      {iframeSrc ? (
        <iframe
          src={iframeSrc}
          className="w-full flex-1 bg-white"
          title={ws.name}
          sandbox="allow-scripts allow-same-origin allow-forms allow-popups"
        />
      ) : (
        <div className="flex flex-1 flex-col items-center justify-center gap-2 px-6 text-center">
          <p className="text-sm text-foreground/80">
            This project hasn&apos;t been published yet.
          </p>
          <p className="max-w-md text-xs text-muted-foreground">
            Generate a design, or publish the app (Workbench → Publish), then
            the link will render here.
          </p>
        </div>
      )}
    </div>
  );
}
