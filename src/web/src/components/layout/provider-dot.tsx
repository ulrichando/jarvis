import type { Provider } from "@/lib/ai/models-meta";
import { cn } from "@/lib/utils";

const COLORS: Record<Provider, string> = {
  anthropic: "bg-orange-400",
  openai: "bg-emerald-400",
  google: "bg-sky-400",
  deepseek: "bg-indigo-400",
  kimi: "bg-fuchsia-400",
  groq: "bg-amber-400",
  ollama: "bg-teal-400",
};

export function ProviderDot({
  provider,
  className,
}: {
  provider: Provider;
  className?: string;
}) {
  return (
    <span
      className={cn(
        "inline-block size-1.5 shrink-0 rounded-full",
        COLORS[provider],
        className,
      )}
    />
  );
}
