"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { ArtifactKind } from "@/lib/actions/types";

export type { ArtifactKind };

export type ArtifactSummary = {
  id: string;
  conversationId: string;
  slug: string;
  title: string;
  kind: ArtifactKind;
  createdAt: string;
  updatedAt: string;
  shareToken: string | null;
  shareExpiresAt: string | null;
  // Latest version content — for the gallery thumbnail (no per-card fetch).
  latestContent: string;
  latestLanguage: string | null;
};

export type ArtifactVersionT = {
  id: string;
  artifactId: string;
  version: number;
  content: string;
  language: string | null;
  messageId: string | null;
  createdAt: string;
};

export type ArtifactWithVersions = ArtifactSummary & {
  versions: ArtifactVersionT[];
};

async function fetchJson<T>(url: string, init?: RequestInit): Promise<T> {
  const res = await fetch(url, init);
  if (!res.ok) throw new Error(await res.text());
  return res.json() as Promise<T>;
}

// Gallery list (metadata, newest first).
export function useAllArtifacts() {
  return useQuery({
    queryKey: ["artifacts"],
    queryFn: () =>
      fetchJson<{ artifacts: ArtifactSummary[] }>("/api/artifacts").then(
        (d) => d.artifacts,
      ),
  });
}

// A conversation's artifacts WITH versions — hydrates the in-chat panel on
// reload (live turns drive the panel from parser state instead).
export function useConversationArtifacts(conversationId: string | undefined) {
  return useQuery({
    queryKey: ["artifacts", "conversation", conversationId],
    enabled: !!conversationId,
    queryFn: () =>
      fetchJson<{ artifacts: ArtifactWithVersions[] }>(
        `/api/artifacts?conversationId=${conversationId}`,
      ).then((d) => d.artifacts),
  });
}

export function useArtifact(id: string | undefined) {
  return useQuery({
    queryKey: ["artifact", id],
    enabled: !!id,
    queryFn: () =>
      fetchJson<{ artifact: ArtifactWithVersions }>(`/api/artifacts/${id}`).then(
        (d) => d.artifact,
      ),
  });
}

export function useRenameArtifact() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, title }: { id: string; title: string }) =>
      fetchJson<{ ok: true }>(`/api/artifacts/${id}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ title }),
      }),
    onSuccess: (_d, { id }) => {
      qc.invalidateQueries({ queryKey: ["artifacts"] });
      qc.invalidateQueries({ queryKey: ["artifact", id] });
    },
  });
}

export function useDeleteArtifact() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) =>
      fetch(`/api/artifacts/${id}`, { method: "DELETE" }).then((r) => {
        if (!r.ok && r.status !== 404) throw new Error(r.statusText);
      }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["artifacts"] }),
  });
}

export function usePublishArtifact() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) =>
      fetchJson<{ token: string; url: string; expiresAt: string }>(
        `/api/artifacts/${id}/publish`,
        { method: "POST" },
      ),
    onSuccess: (_d, id) => {
      qc.invalidateQueries({ queryKey: ["artifacts"] });
      qc.invalidateQueries({ queryKey: ["artifact", id] });
    },
  });
}

// One-time scan of chat history → populate the gallery from past chats.
export function useBackfillArtifacts() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () =>
      fetchJson<{ scanned: number; artifacts: number }>(
        "/api/artifacts/backfill",
        { method: "POST" },
      ),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["artifacts"] }),
  });
}

export function useUnpublishArtifact() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) =>
      fetch(`/api/artifacts/${id}/publish`, { method: "DELETE" }).then((r) => {
        if (!r.ok && r.status !== 404) throw new Error(r.statusText);
      }),
    onSuccess: (_d, id) => {
      qc.invalidateQueries({ queryKey: ["artifacts"] });
      qc.invalidateQueries({ queryKey: ["artifact", id] });
    },
  });
}
