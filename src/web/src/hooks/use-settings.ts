"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { Provider } from "@/lib/ai/models-meta";
import type { Settings } from "@/lib/settings/schema";

export type RedactedSettings = Omit<Settings, "providers"> & {
  providers: Record<
    Provider,
    { hasKey: boolean; keyPreview?: string; baseURL?: string }
  >;
};

async function fetchJson<T>(url: string, init?: RequestInit): Promise<T> {
  const res = await fetch(url, init);
  if (!res.ok) throw new Error(await res.text());
  return res.json() as Promise<T>;
}

export function useSettings() {
  return useQuery({
    queryKey: ["settings"],
    queryFn: () => fetchJson<RedactedSettings>("/api/settings"),
    staleTime: Infinity,
  });
}

export type SettingsPatch = {
  user?: { name?: string };
  defaults?: {
    model?: string;
    systemPrompt?: string;
    temperature?: number;
  };
  providers?: Partial<
    Record<Provider, { apiKey?: string | null; baseURL?: string | null }>
  >;
  appearance?: {
    fontSize?: "sm" | "md" | "lg";
    density?: "compact" | "cozy";
  };
};

export function useUpdateSettings() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (patch: SettingsPatch) =>
      fetchJson<RedactedSettings>("/api/settings", {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(patch),
      }),
    onSuccess: (data) => qc.setQueryData(["settings"], data),
  });
}

export function useTestProvider() {
  return useMutation({
    mutationFn: (provider: Provider) =>
      fetchJson<{
        ok: boolean;
        latencyMs?: number;
        reply?: string;
        error?: string;
      }>("/api/settings/test-provider", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ provider }),
      }).catch((e: Error) => ({
        ok: false as const,
        error: e.message,
      })),
  });
}
