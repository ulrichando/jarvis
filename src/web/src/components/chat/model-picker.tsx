"use client";

import { ChevronDown, Check } from "lucide-react";
import { Fragment } from "react";
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
  modelsByProvider,
  type ModelId,
} from "@/lib/ai/models-meta";
import { getProviderUX } from "@/lib/ai/provider-ux";
import { useChatStore } from "@/stores/chat";

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
  const active = MODELS_META[model] ?? MODELS_META[DEFAULT_MODEL];
  const groups = modelsByProvider();
  const providerLabel =
    groups.find((g) => g.provider === active.provider)?.label ?? "";
  const ux = getProviderUX(active.provider);
  const primaryLabel =
    ux.modelShortLabel?.(active.label, active.id) ?? shortLabel(active.label);
  const showSub = !ux.modelShortLabel?.(active.label, active.id);

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
        side="bottom"
        sideOffset={6}
        className="w-64 p-1"
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
                return (
                  <DropdownMenuItem
                    key={m.id}
                    onClick={() => setModel(m.id as ModelId)}
                    className="gap-1.5 py-1 text-[12.5px]"
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
