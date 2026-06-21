import { notFound } from "next/navigation";
import { ArrowRight, Sparkles } from "lucide-react";
import Link from "next/link";
import { Button } from "@/components/ui/button";
import { PROVIDER_FEATURES } from "@/lib/ai/features";

// JARVIS-branded landing for a top-level feature (Computer use / Customize /
// Artifacts). These are JARVIS's own features — the URL and header deliberately
// carry NO provider name (the old /anthropic/<slug> route redirects here).
export function FeatureLanding({ slug }: { slug: string }) {
  const feature = PROVIDER_FEATURES.anthropic.find((f) => f.slug === slug);
  if (!feature) return notFound();
  const Icon = feature.icon;
  const siblings = PROVIDER_FEATURES.anthropic.filter((x) => x.slug !== slug && x.href);

  return (
    <div className="flex h-full flex-col">
      <header className="flex h-12 shrink-0 items-center gap-2 border-b border-border/60 px-5 font-mono text-[11px] uppercase tracking-[0.18em] text-muted-foreground">
        <span className="text-foreground/80">{feature.label}</span>
      </header>

      <div className="flex-1 overflow-y-auto">
        <div className="mx-auto max-w-3xl px-6 py-12">
          <div className="flex items-start gap-4">
            <div className="flex size-12 shrink-0 items-center justify-center rounded-xl border border-primary/30 bg-primary/10">
              <Icon className="size-5 text-primary" />
            </div>
            <div className="min-w-0 flex-1">
              <h1 className="text-2xl font-semibold tracking-tight">{feature.label}</h1>
              <p className="mt-2 text-[15px] leading-7 text-muted-foreground">{feature.description}</p>
            </div>
          </div>

          <div className="mt-10 flex flex-wrap items-center gap-2">
            <Button render={<Link href="/chat" />} nativeButton={false} size="sm" className="rounded-md">
              Start in chat <ArrowRight className="size-3.5" />
            </Button>
            <span className="inline-flex items-center gap-1.5 rounded-full border border-border/60 px-3 py-1 font-mono text-[10px] uppercase tracking-wider text-muted-foreground">
              <Sparkles className="size-3 text-primary" />
              workspace landing soon
            </span>
          </div>

          {siblings.length > 0 && (
            <div className="mt-12">
              <div className="mb-3 font-mono text-[10px] uppercase tracking-[0.18em] text-muted-foreground">
                More
              </div>
              <div className="grid grid-cols-1 gap-2 md:grid-cols-2">
                {siblings.map((s) => {
                  const SIcon = s.icon;
                  return (
                    <Link
                      key={s.slug}
                      href={s.href ?? `/${s.slug}`}
                      className="group flex items-start gap-3 rounded-lg border border-border/60 bg-card/40 p-3 transition-colors hover:border-primary/40 hover:bg-card"
                    >
                      <SIcon className="size-4 shrink-0 text-primary" />
                      <div className="min-w-0">
                        <div className="text-sm font-medium">{s.label}</div>
                        <div className="mt-0.5 line-clamp-2 text-xs leading-4 text-muted-foreground">{s.description}</div>
                      </div>
                    </Link>
                  );
                })}
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
