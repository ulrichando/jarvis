"use client";

import { Loader2 } from "lucide-react";
import { useArtifact } from "@/hooks/use-artifacts";
import { ArtifactViewer } from "./artifact-viewer";

// Authed full-page view of a single artifact (the gallery card target +
// the in-chat panel's "open in new tab"). Reuses ArtifactViewer.
export function ArtifactDetail({ id }: { id: string }) {
  const { data: artifact, isLoading, isError } = useArtifact(id);

  if (isLoading) {
    return (
      <div className="flex h-full items-center justify-center">
        <Loader2 className="size-5 animate-spin text-muted-foreground" />
      </div>
    );
  }
  if (isError || !artifact || artifact.versions.length === 0) {
    return (
      <div className="flex h-full items-center justify-center text-sm text-muted-foreground">
        Artifact not found.
      </div>
    );
  }

  return (
    <div className="h-full">
      <ArtifactViewer
        title={artifact.title}
        kind={artifact.kind}
        language={artifact.versions.at(-1)?.language ?? null}
        versions={artifact.versions.map((v) => v.content)}
        artifactId={artifact.id}
        shareToken={artifact.shareToken}
      />
    </div>
  );
}
