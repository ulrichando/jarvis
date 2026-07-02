import { notFound, redirect } from "next/navigation";
import { ArrowRight, Rocket } from "lucide-react";
import Link from "next/link";
import { Button } from "@/components/ui/button";
import { getFeature, PROVIDER_FEATURES } from "@/lib/ai/features";
import { PROVIDER_LABEL, type Provider } from "@/lib/ai/models-meta";
import { ProviderDot } from "@/components/layout/provider-dot";

const VALID_PROVIDERS = Object.keys(PROVIDER_FEATURES) as Provider[];

export function generateStaticParams() {
  const out: Array<{ provider: string; feature: string }> = [];
  for (const [provider, features] of Object.entries(PROVIDER_FEATURES)) {
    // Skip features that have a real top-level page — e.g. /anthropic/projects
    // would shadow /projects. The sidebar already routes around them.
    for (const f of features) {
      if (f.href) continue;
      out.push({ provider, feature: f.slug });
    }
  }
  return out;
}

export default async function FeaturePage(
  props: PageProps<"/[provider]/[feature]">,
) {
  const { provider, feature } = await props.params;

  if (!VALID_PROVIDERS.includes(provider as Provider)) return notFound();

  const resolved = getFeature(provider, feature);
  if (!resolved) return notFound();
  if (resolved.feature.href) redirect(resolved.feature.href);

  const { feature: f, provider: p } = resolved;
  const siblings = PROVIDER_FEATURES[p].filter((x) => x.slug !== f.slug);
  const Icon = f.icon;

  return (
    <div className="flex h-full flex-col">
      <header className="flex h-12 shrink-0 items-center justify-between border-b border-border/60 px-5">
        <div className="flex items-center gap-2 font-mono text-[11px] uppercase tracking-[0.18em] text-muted-foreground">
          <ProviderDot provider={p} />
          <span>{PROVIDER_LABEL[p]}</span>
          <span className="text-muted-foreground/40">/</span>
          <span className="text-foreground/80">{f.label}</span>
        </div>
      </header>

      <div className="flex-1 overflow-y-auto">
        <div className="mx-auto max-w-3xl px-6 py-12">
          <div className="flex items-start gap-4">
            <div className="flex size-12 shrink-0 items-center justify-center rounded-xl border border-primary/30 bg-primary/10">
              <Icon className="size-5 text-primary" />
            </div>
            <div className="min-w-0 flex-1">
              <h1 className="text-2xl font-semibold tracking-tight">{f.label}</h1>
              <p className="mt-2 text-[15px] leading-7 text-muted-foreground">
                {f.description}
              </p>
            </div>
          </div>

          <div className="mt-10 flex flex-wrap items-center gap-2">
            <Button
              render={<Link href="/chat" />}
              nativeButton={false}
              size="sm"
              className="rounded-md"
            >
              Start in chat <ArrowRight className="size-3.5" />
            </Button>
            <span className="inline-flex items-center gap-1.5 rounded-full border border-border/60 px-3 py-1 font-mono text-[10px] uppercase tracking-wider text-muted-foreground">
              <Rocket className="size-3 text-primary" />
              workspace landing soon
            </span>
          </div>

          {siblings.length > 0 && (
            <div className="mt-12">
              <div className="mb-3 font-mono text-[10px] uppercase tracking-[0.18em] text-muted-foreground">
                More from {PROVIDER_LABEL[p]}
              </div>
              <div className="grid grid-cols-1 gap-2 md:grid-cols-2">
                {siblings.map((s) => {
                  const SIcon = s.icon;
                  return (
                    <Link
                      key={s.slug}
                      href={`/${p}/${s.slug}`}
                      className="group flex items-start gap-3 rounded-lg border border-border/60 bg-card/40 p-3 transition-colors hover:border-primary/40 hover:bg-card"
                    >
                      <SIcon className="size-4 shrink-0 text-primary" />
                      <div className="min-w-0">
                        <div className="text-sm font-medium">{s.label}</div>
                        <div className="mt-0.5 line-clamp-2 text-xs leading-4 text-muted-foreground">
                          {s.description}
                        </div>
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
