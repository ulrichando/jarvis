"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { Provider } from "@/lib/ai/models-meta";
import type { Settings } from "@/lib/settings/schema";

export type RedactedSettings = Omit<Settings, "providers" | "integrations"> & {
  providers: Record<
    Provider,
    {
      hasKey: boolean;
      keyPreview?: string;
      keySource?: "settings" | "env";
      baseURL?: string;
    }
  >;
  integrations: {
    github: {
      hasToken: boolean;
      tokenPreview?: string;
      defaultOwner?: string;
    };
  };
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
    // Was Infinity — a long-lived tab then NEVER saw settings changed from
    // another tab/device, which reads as "settings don't work".
    staleTime: 30_000,
  });
}

export type SettingsPatch = {
  user?: {
    name?: string;
    callName?: string;
    jobTitle?: string;
    preferences?: string;
    voice?: string; // Kokoro voice id, e.g. "af_heart"
  };
  notifications?: { responseCompletions?: boolean };
  capabilities?: {
    markdown?: boolean;
    codeHighlight?: boolean;
    streaming?: boolean;
  };
  defaults?: {
    model?: string;
    imageModel?: string;
    systemPrompt?: string;
    temperature?: number;
  };
  providers?: Partial<
    Record<Provider, { apiKey?: string | null; baseURL?: string | null }>
  >;
  connections?: { ollama?: { baseURL?: string | null } };
  appearance?: {
    fontSize?: "sm" | "md" | "lg";
    density?: "compact" | "cozy";
  };
  integrations?: {
    github?: {
      token?: string | null;
      defaultOwner?: string | null;
    };
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
