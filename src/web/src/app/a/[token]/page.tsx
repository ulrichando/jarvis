import { Package } from "lucide-react";
import { getArtifactByShareToken } from "@/lib/artifacts/store";
import { bundleReactSource } from "@/lib/artifacts/bundle";
import { ArtifactRender } from "@/components/artifacts/artifact-render";

// Public, read-only view of a published artifact. Reachable WITHOUT login
// (allowlisted in src/proxy.ts via the `/a` prefix). React is bundled
// server-side here so the logged-out viewer needs no authed bundle fetch.
// Unknown/expired tokens get a generic screen (no existence probing).
export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export default async function PublicArtifactPage({
  params,
}: {
  params: Promise<{ token: string }>;
}) {
  const { token } = await params;
  const art = await getArtifactByShareToken(token);

  if (!art) {
    return (
      <div className="flex h-screen items-center justify-center bg-background px-6 text-center text-sm text-muted-foreground">
        This shared artifact is unavailable or the link has expired.
      </div>
    );
  }

  let bundledJs: string | undefined;
  if (art.kind === "react") {
    const out = await bundleReactSource(art.content);
    if ("js" in out) bundledJs = out.js;
  }

  return (
    <div className="flex h-screen flex-col bg-background">
      <header className="flex h-12 shrink-0 items-center gap-2 border-b border-border/60 px-4">
        <Package className="size-4 text-muted-foreground" />
        <span className="truncate text-[13px] font-medium text-foreground">
          {art.title}
        </span>
        <span className="ml-auto shrink-0 text-[11px] text-muted-foreground">
          Shared via JARVIS
        </span>
      </header>
      <div className="min-h-0 flex-1 overflow-hidden">
        <ArtifactRender
          kind={art.kind}
          content={art.content}
          language={art.language}
          mode="preview"
          bundledJs={bundledJs}
        />
      </div>
    </div>
  );
}
