import type { Provider } from "@/lib/ai/models-meta";
import { cn } from "@/lib/utils";

const COLORS: Record<Provider, string> = {
  anthropic: "bg-orange-400",
  openai: "bg-emerald-400",
  google: "bg-sky-400",
  deepseek: "bg-indigo-400",
  kimi: "bg-fuchsia-400",
  ollama: "bg-teal-400",
};

export function ProviderDot({
  provider,
  className,
  muted = false,
}: {
  provider: Provider;
  className?: string;
  // When true, render a dim neutral dot instead of the brand color — the
  // settings list uses this to encode "not configured", so the dot means
  // something rather than reading as a false amber/green status signal.
  // Decorative in both cases (sits beside the provider name) → aria-hidden.
  muted?: boolean;
}) {
  return (
    <span
      aria-hidden="true"
      className={cn(
        "inline-block size-1.5 shrink-0 rounded-full",
        muted ? "bg-muted-foreground/30" : COLORS[provider],
        className,
      )}
    />
  );
}
