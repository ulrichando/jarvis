"use client";

import { ChevronDown, Package } from "lucide-react";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import type { ArtifactKind } from "@/lib/actions/types";
import { ArtifactViewer } from "./artifact-viewer";

export type PanelArtifact = {
  id?: string;
  slug: string;
  title: string;
  kind: ArtifactKind;
  language?: string | null;
  versions: string[];
  shareToken?: string | null;
};

type Props = {
  artifacts: PanelArtifact[];
  activeSlug: string;
  onActiveSlugChange: (slug: string) => void;
  onClose: () => void;
};

export function ArtifactSidePanel({
  artifacts,
  activeSlug,
  onActiveSlugChange,
  onClose,
}: Props) {
  const active =
    artifacts.find((a) => a.slug === activeSlug) ?? artifacts[artifacts.length - 1];
  if (!active) return null;

  const switcher =
    artifacts.length > 1 ? (
      <DropdownMenu>
        <DropdownMenuTrigger
          render={
            <button className="flex min-w-0 items-center gap-1.5 rounded-md px-1.5 py-1 text-[13px] font-medium hover:bg-accent">
              <Package className="size-3.5 shrink-0 text-muted-foreground" />
              <span className="truncate">{active.title}</span>
              <ChevronDown className="size-3.5 shrink-0 text-muted-foreground" />
            </button>
          }
        />
        <DropdownMenuContent align="start" className="max-w-72">
          {artifacts.map((a) => (
            <DropdownMenuItem
              key={a.slug}
              onClick={() => onActiveSlugChange(a.slug)}
            >
              <Package className="size-3.5 text-muted-foreground" />
              <span className="truncate">{a.title}</span>
            </DropdownMenuItem>
          ))}
        </DropdownMenuContent>
      </DropdownMenu>
    ) : (
      <span className="flex min-w-0 items-center gap-1.5">
        <Package className="size-3.5 shrink-0 text-muted-foreground" />
        <span className="truncate text-[13px] font-medium">{active.title}</span>
      </span>
    );

  return (
    <ArtifactViewer
      // Remount when switching artifacts so version/mode reset cleanly.
      key={active.slug}
      title={active.title}
      kind={active.kind}
      language={active.language}
      versions={active.versions}
      artifactId={active.id}
      shareToken={active.shareToken}
      headerLeft={switcher}
      onClose={onClose}
    />
  );
}
