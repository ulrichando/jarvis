"use client";
import { Cpu, ChevronDown, Check } from "lucide-react";
import { DropdownMenu, DropdownMenuTrigger, DropdownMenuContent, DropdownMenuItem } from "@/components/ui/dropdown-menu";

export const CU_MODELS = [
  { id: "claude-sonnet-4-6", label: "Claude Sonnet 4.6", hint: "Balanced", provider: "anthropic" },
  { id: "claude-opus-4-8", label: "Claude Opus 4.8", hint: "Most capable", provider: "anthropic" },
  { id: "claude-haiku-4-5", label: "Claude Haiku 4.5", hint: "Fastest", provider: "anthropic" },
  { id: "gpt-5.5", label: "GPT-5.5", hint: "OpenAI", provider: "openai" },
  { id: "gemini-3-flash-preview", label: "Gemini 3 Flash", hint: "Google", provider: "gemini" },
] as const;

export function ModelPicker({
  model, setModel, disabled, providers,
}: { model: string; setModel: (m: string) => void; disabled?: boolean; providers?: Record<string, boolean> }) {
  const current = CU_MODELS.find((m) => m.id === model) ?? CU_MODELS[0];
  return (
    <DropdownMenu>
      <DropdownMenuTrigger
        render={
          <button
            disabled={disabled}
            className="inline-flex items-center gap-2 rounded-lg border border-border/40 bg-muted/40 px-2.5 py-1.5 text-[12px] text-foreground transition-colors hover:border-border/60 disabled:opacity-50"
            title="Model that drives the desktop"
          />
        }
      >
        <Cpu className="size-3.5 text-muted-foreground" />
        {current.label}
        <span className="rounded bg-primary/10 px-1 py-px text-[8.5px] font-bold tracking-wide text-primary">NATIVE</span>
        <ChevronDown className="size-3 opacity-60" />
      </DropdownMenuTrigger>
      <DropdownMenuContent align="start" className="min-w-56">
        {CU_MODELS.map((m) => {
          const avail = !providers || providers[m.provider] !== false;
          return (
            <DropdownMenuItem key={m.id} disabled={!avail} onClick={() => { if (avail) setModel(m.id); }} className="flex items-center justify-between gap-3">
              <span className="flex items-center gap-2">
                {m.id === model ? <Check className="size-3.5 text-primary" /> : <span className="size-3.5" />}
                {m.label}
              </span>
              <span className="text-[10px] text-muted-foreground">{avail ? m.hint : "no key"}</span>
            </DropdownMenuItem>
          );
        })}
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
