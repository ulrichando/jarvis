"use client";

import { ChevronDown, Check, KeyRound } from "lucide-react";
import { Fragment } from "react";
import { useQuery } from "@tanstack/react-query";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuGroup,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import {
  DEFAULT_MODEL,
  MODELS_META,
  buildOllamaMeta,
  isOllamaId,
  modelsByProvider,
  ollamaIdToTag,
  type ModelId,
  type ModelMeta,
  type Provider,
} from "@/lib/ai/models-meta";
import { getProviderUX } from "@/lib/ai/provider-ux";
import { useChatStore } from "@/stores/chat";
import { cn } from "@/lib/utils";

// Reads /api/providers/available — server tells us which provider keys
// are configured in env. Picker dims models from providers without
// keys (still visible, click shows a toast). Once a key is added in
// .env.local + dev restarted, this query reflects it within the
// staleTime window (60 s).
type AvailableMap = Record<Provider, boolean>;
function useAvailableProviders() {
  return useQuery<AvailableMap>({
    queryKey: ["providers", "available"],
    queryFn: async () => {
      const r = await fetch("/api/providers/available");
      if (!r.ok) throw new Error("provider availability fetch failed");
      return r.json();
    },
    staleTime: 60_000,
    refetchOnWindowFocus: false,
  });
}

// Reads /api/providers/ollama-models — the server enumerates the local Ollama
// daemon so every pulled model is selectable, not just the two static entries.
// Returns [] when the daemon is offline, so the picker degrades to statics.
type DiscoveredOllama = { id: string; tag: string };
function useDiscoveredOllamaModels() {
  return useQuery<DiscoveredOllama[]>({
    queryKey: ["providers", "ollama-models"],
    queryFn: async () => {
      const r = await fetch("/api/providers/ollama-models");
      if (!r.ok) throw new Error("ollama models fetch failed");
      const data = (await r.json()) as { models?: DiscoveredOllama[] };
      return data.models ?? [];
    },
    staleTime: 60_000,
    refetchOnWindowFocus: false,
  });
}

/**
 * Strips redundant brand prefixes so the picker reads like claude.ai's
 * compact "Opus 4.7" label instead of the full "Claude Opus 4.7".
 */
function shortLabel(full: string): string {
  return full.replace(/^Claude\s+/, "");
}

/**
 * Some models ship a mode hint that claude.ai surfaces as a muted sub-label
 * (e.g., "Adaptive", "Reasoner"). For anything else we fall back to the
 * provider as the muted tag.
 */
function subLabel(id: string, providerLabel: string): string {
  if (id.startsWith("claude-opus") || id.startsWith("claude-sonnet")) {
    return "Adaptive";
  }
  if (id === "deepseek-reasoner") return "Reasoner";
  if (id === "o3") return "Reasoning";
  if (id === "kimi-k2-instant") return "Instant";
  if (id === "kimi-k2-thinking") return "Thinking";
  if (id === "kimi-k2-agent") return "Agent";
  if (id === "kimi-k2-swarm") return "Swarm";
  return providerLabel;
}

export function ComposerModelPicker() {
  const model = useChatStore((s) => s.model);
  const setModel = useChatStore((s) => s.setModel);
  const { data: available } = useAvailableProviders();
  const { data: discovered } = useDiscoveredOllamaModels();

  // The picker lists ONLY ollama models actually pulled into the local daemon
  // (from live discovery) — no hardcoded fallback, so nothing appears when the
  // user hasn't downloaded a local model. The static ollama-* registry entries
  // remain for routing/test-provider but are not shown here.
  const installedOllama: ModelMeta[] = (discovered ?? []).map((d) =>
    buildOllamaMeta(d.tag),
  );

  // Swap the static ollama group's models for what's installed, and drop the
  // ollama group entirely when nothing is pulled.
  const groups = modelsByProvider()
    .map((g) =>
      g.provider === "ollama" ? { ...g, models: installedOllama } : g,
    )
    .filter((g) => g.provider !== "ollama" || g.models.length > 0);

  // Resolve the active model — including a discovered id not in MODELS_META.
  const active =
    MODELS_META[model] ??
    (isOllamaId(model)
      ? buildOllamaMeta(ollamaIdToTag(model) ?? model)
      : undefined) ??
    MODELS_META[DEFAULT_MODEL];

  const providerLabel =
    groups.find((g) => g.provider === active.provider)?.label ?? "";
  const ux = getProviderUX(active.provider);
  const primaryLabel =
    ux.modelShortLabel?.(active.label, active.id) ?? shortLabel(active.label);
  const showSub = !ux.modelShortLabel?.(active.label, active.id);

  const isProviderAvailable = (p: Provider): boolean => {
    // Until the availability map loads, optimistically allow everything
    // — avoids one render flash where every model looks dim.
    if (!available) return true;
    return available[p] ?? false;
  };

  return (
    <DropdownMenu>
      <DropdownMenuTrigger
        render={
          <Button
            variant="ghost"
            size="sm"
            className="h-7 gap-1.5 px-2 text-[13px] text-foreground hover:text-foreground"
          />
        }
      >
        <span>{primaryLabel}</span>
        {showSub && (
          <span className="text-muted-foreground">
            {subLabel(active.id, providerLabel)}
          </span>
        )}
        <ChevronDown className="size-3 text-muted-foreground" />
      </DropdownMenuTrigger>
      <DropdownMenuContent
        align="end"
        // Drop downward — matches Claude's behaviour. Base UI
        // auto-flips to "top" if there isn't enough room below,
        // so this is a hint, not a hard pin.
        side="bottom"
        sideOffset={6}
        // max-h matches Claude.ai's compact picker height. The list
        // grows past it as more providers/models land, so the inner
        // scroll keeps the popover from running off-screen.
        className="w-64 max-h-100 overflow-y-auto p-1"
      >
        {groups.map((g, gi) => (
          <Fragment key={g.provider}>
            {gi > 0 && <DropdownMenuSeparator />}
            <DropdownMenuGroup>
              <DropdownMenuLabel className="px-2 pt-1.5 pb-0.5 font-mono text-[9px] uppercase tracking-[0.18em]">
                {g.label}
              </DropdownMenuLabel>
              {g.models.map((m) => {
                const isActive = m.id === model;
                const enabled = isProviderAvailable(g.provider);
                return (
                  <DropdownMenuItem
                    key={m.id}
                    onClick={() => {
                      if (!enabled) {
                        toast.error(
                          `${g.label} key not set`,
                          {
                            description:
                              `Add the API key in src/web/.env.local and restart the dev server to enable ${shortLabel(m.label)}.`,
                          },
                        );
                        return;
                      }
                      setModel(m.id as ModelId);
                    }}
                    className={cn(
                      "gap-1.5 py-1 text-[13px]",
                      !enabled && "opacity-50",
                    )}
                  >
                    <Check
                      className={
                        isActive
                          ? "size-3 text-primary"
                          : "size-3 opacity-0"
                      }
                    />
                    <span className="truncate">{shortLabel(m.label)}</span>
                    {m.badge && (
                      <span className="ml-auto rounded-sm bg-primary/15 px-1.5 py-px text-[9px] font-medium uppercase tracking-wide text-primary">
                        {m.badge}
                      </span>
                    )}
                    {!enabled && (
                      <KeyRound
                        className="ml-auto size-3 text-muted-foreground/70"
                        aria-label="API key required"
                      />
                    )}
                  </DropdownMenuItem>
                );
              })}
            </DropdownMenuGroup>
          </Fragment>
        ))}
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
