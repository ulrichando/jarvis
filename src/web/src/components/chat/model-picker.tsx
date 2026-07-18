"use client";

import { ChevronDown, Check, Clock, KeyRound, Info } from "lucide-react";
import { Fragment } from "react";
import { useQuery } from "@tanstack/react-query";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Switch } from "@/components/ui/switch";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuGroup,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuSub,
  DropdownMenuSubContent,
  DropdownMenuSubTrigger,
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
import { useChatStore, EFFORTS, DEFAULT_EFFORT, type Effort } from "@/stores/chat";
import { cn } from "@/lib/utils";

// Reads /api/providers/available — server tells us which provider keys
// are configured in env. Picker dims models from providers without keys.
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

// Reads /api/providers/ollama-models — enumerates the local Ollama daemon so
// every pulled model is selectable. Returns [] when the daemon is offline.
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

/** Compact label like claude.ai's "Opus 4.8" (drops the "Claude " prefix). */
function shortLabel(full: string): string {
  return full.replace(/^Claude\s+/, "");
}

// Curated flagships shown up top, claude.ai-style, with a punchy one-line
// description. Everything else lives under "More models".
const TOP_MODEL_IDS: string[] = [
  "claude-fable-5",
  "claude-opus-4-8",
  "claude-sonnet-4-6",
  "claude-haiku-4-5",
];
const TOP_DESCRIPTION: Record<string, string> = {
  "claude-fable-5": "For your toughest challenges",
  "claude-opus-4-8": "For complex tasks",
  "claude-sonnet-4-6": "Most efficient for everyday tasks",
  "claude-haiku-4-5": "Fastest for quick answers",
};
const TOP_NOTE: Record<string, string> = {
  "claude-fable-5": "Included until July 19",
};
const TOP_LABEL: Record<string, string> = {
  "claude-sonnet-4-6": "Sonnet 5",
};

const EFFORT_LABEL: Record<Effort, string> = {
  low: "Low",
  medium: "Medium",
  high: "High",
  extra: "Extra",
  max: "Max",
};

export function ComposerModelPicker() {
  const model = useChatStore((s) => s.model);
  const setModel = useChatStore((s) => s.setModel);
  const effort = useChatStore((s) => s.effort);
  const setEffort = useChatStore((s) => s.setEffort);
  const thinking = useChatStore((s) => s.thinking);
  const setThinking = useChatStore((s) => s.setThinking);
  const { data: available } = useAvailableProviders();
  const { data: discovered } = useDiscoveredOllamaModels();

  const installedOllama: ModelMeta[] = (discovered ?? []).map((d) =>
    buildOllamaMeta(d.tag),
  );

  const groups = modelsByProvider()
    .map((g) =>
      g.provider === "ollama" ? { ...g, models: installedOllama } : g,
    )
    .filter((g) => g.provider !== "ollama" || g.models.length > 0);

  const active =
    MODELS_META[model] ??
    (isOllamaId(model)
      ? buildOllamaMeta(ollamaIdToTag(model) ?? model)
      : undefined) ??
    MODELS_META[DEFAULT_MODEL];

  const ux = getProviderUX(active.provider);
  const primaryLabel =
    ux.modelShortLabel?.(active.label, active.id) ?? shortLabel(active.label);

  const isProviderAvailable = (p: Provider): boolean => {
    if (!available) return true; // optimistic until the map loads
    return available[p] ?? false;
  };

  const selectModel = (m: ModelMeta, providerLabel: string) => {
    if (!isProviderAvailable(m.provider)) {
      toast.error(`${providerLabel} key not set`, {
        description: `Add the API key in src/web/.env.local and restart the dev server to enable ${shortLabel(m.label)}.`,
      });
      return;
    }
    setModel(m.id as ModelId);
  };

  const topModels = TOP_MODEL_IDS.map((id) => MODELS_META[id]).filter(
    Boolean,
  ) as ModelMeta[];

  // "More models" = every group minus the curated flagships (drops now-empty groups).
  const moreGroups = groups
    .map((g) => ({
      ...g,
      models: g.models.filter((m) => !TOP_MODEL_IDS.includes(m.id)),
    }))
    .filter((g) => g.models.length > 0);

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
        <span className="text-muted-foreground">{EFFORT_LABEL[effort]}</span>
        <ChevronDown className="size-3 text-muted-foreground" />
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" side="bottom" sideOffset={6} className="w-72 p-1">
        {/* ── Curated flagships ── */}
        {topModels.map((m) => {
          const isActive = m.id === model;
          const enabled = isProviderAvailable(m.provider);
          return (
            <DropdownMenuItem
              key={m.id}
              onClick={() => selectModel(m, "Anthropic")}
              className={cn(
                "flex items-start gap-2 py-1.5",
                !enabled && "opacity-50",
              )}
            >
              <div className="min-w-0 flex-1">
                <div className="flex items-center gap-1.5">
                  <span className="text-[13px] font-medium text-foreground">
                    {TOP_LABEL[m.id] ?? shortLabel(m.label)}
                  </span>
                  {TOP_NOTE[m.id] && (
                    <>
                      <Clock className="size-3 shrink-0 text-muted-foreground" />
                      <span className="text-[11px] text-muted-foreground">
                        {TOP_NOTE[m.id]}
                      </span>
                    </>
                  )}
                </div>
                <div className="text-[11.5px] leading-snug text-muted-foreground">
                  {TOP_DESCRIPTION[m.id] ?? m.description}
                </div>
              </div>
              {isActive ? (
                <Check className="mt-0.5 size-3.5 shrink-0 text-primary" />
              ) : !enabled ? (
                <KeyRound
                  className="mt-0.5 size-3 shrink-0 text-muted-foreground/70"
                  aria-label="API key required"
                />
              ) : (
                <span className="size-3.5 shrink-0" />
              )}
            </DropdownMenuItem>
          );
        })}

        <DropdownMenuSeparator />

        {/* ── Effort submenu ── */}
        <DropdownMenuSub>
          {/* [&>svg]:!ml-0 kills the SubTrigger's auto-chevron ml-auto so it
              doesn't split the free space with the "High" span — otherwise a
              gap opens between the effort value and the ">" chevron. With the
              chevron neutralized, the value's ml-auto pushes value+chevron to
              the right, adjacent (gap-1.5 apart). */}
          <DropdownMenuSubTrigger className="py-1 text-[13px] [&>svg]:!ml-0">
            <span>Effort</span>
            <span className="ml-auto text-muted-foreground">
              {EFFORT_LABEL[effort]}
            </span>
          </DropdownMenuSubTrigger>
          <DropdownMenuSubContent side="left" className="w-64 p-1">
            <p className="px-2 pt-1.5 pb-2 text-[11.5px] leading-snug text-muted-foreground">
              Higher effort means more thorough responses, but takes longer and
              uses your limits faster.
            </p>
            {EFFORTS.map((e) => {
              const sel = e === effort;
              return (
                <button
                  key={e}
                  type="button"
                  onClick={() => setEffort(e)}
                  className="flex w-full items-center gap-1.5 rounded-md px-2 py-1.5 text-left text-[13px] hover:bg-accent"
                >
                  <span className="text-foreground">{EFFORT_LABEL[e]}</span>
                  {e === DEFAULT_EFFORT && (
                    <span className="rounded-sm bg-muted px-1 py-px text-[9px] font-medium tracking-wide text-muted-foreground">
                      Default
                    </span>
                  )}
                  {e === "max" && (
                    <Info className="size-3 text-muted-foreground/70" />
                  )}
                  {sel && (
                    <Check className="ml-auto size-3.5 shrink-0 text-primary" />
                  )}
                </button>
              );
            })}
            <DropdownMenuSeparator />
            <div
              className="flex items-start gap-2 px-2 py-1.5"
              onClick={(e) => e.stopPropagation()}
            >
              <div className="min-w-0 flex-1">
                <div className="text-[13px] text-foreground">Thinking</div>
                <div className="text-[11.5px] leading-snug text-muted-foreground">
                  Can think for more complex tasks
                </div>
              </div>
              <Switch
                checked={thinking}
                onCheckedChange={setThinking}
                size="sm"
                className="mt-0.5"
              />
            </div>
          </DropdownMenuSubContent>
        </DropdownMenuSub>

        {/* ── More models submenu ── */}
        <DropdownMenuSub>
          <DropdownMenuSubTrigger className="py-1 text-[13px]">
            <span>More models</span>
          </DropdownMenuSubTrigger>
          <DropdownMenuSubContent side="left" className="w-64 max-h-100 overflow-y-auto p-1">
            {moreGroups.map((g, gi) => (
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
                        onClick={() => selectModel(m, g.label)}
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
          </DropdownMenuSubContent>
        </DropdownMenuSub>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
