"use client";

import { Info, RotateCcw } from "lucide-react";
import { Switch } from "@/components/ui/switch";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import { useSettings } from "@/hooks/use-settings";
import { toast } from "sonner";

function SectionHeader({
  title,
  right,
  sub,
}: {
  title: string;
  right?: string;
  sub?: React.ReactNode;
}) {
  return (
    <div className="mb-0">
      <div className="flex items-center justify-between">
        <h2 className="text-[15px] font-semibold">{title}</h2>
        {right && (
          <span className="text-[13px] text-muted-foreground">{right}</span>
        )}
      </div>
      {sub && <div className="mt-0.5">{sub}</div>}
      <div className="mt-2 border-t border-border/60" />
    </div>
  );
}

function BarRow({
  label,
  labelRight,
  sub,
  percent,
  info,
}: {
  label: string;
  labelRight: string;
  sub: string;
  percent: number;
  info?: boolean;
}) {
  return (
    <div className="py-4">
      <div className="flex items-baseline justify-between gap-4">
        <div className="flex items-center gap-1">
          <span className="text-[14px] font-medium">{label}</span>
          {info && <Info className="size-3.5 text-muted-foreground/60 shrink-0" />}
        </div>
        <span className="shrink-0 text-[13px] text-muted-foreground">
          {labelRight}
        </span>
      </div>
      <p className="mt-0.5 text-[13px] text-muted-foreground">{sub}</p>
      <div className="mt-2 h-1.5 w-full overflow-hidden rounded-full bg-muted/50">
        <div
          className={cn(
            "h-full rounded-full transition-all",
            percent >= 80 ? "bg-destructive/70" : "bg-primary",
          )}
          style={{ width: `${Math.min(percent, 100)}%` }}
        />
      </div>
    </div>
  );
}

function AmountRow({
  amount,
  label,
  sub,
  info,
  actions,
}: {
  amount: string;
  label: string;
  sub?: string;
  info?: boolean;
  actions?: React.ReactNode;
}) {
  return (
    <div className="flex items-center justify-between gap-4 py-4">
      <div>
        <div className="flex items-center gap-1">
          <span className="text-[14px] font-semibold">{amount}</span>
          {info && <Info className="size-3.5 text-muted-foreground/60 shrink-0" />}
        </div>
        <p className="mt-0.5 text-[13px] text-muted-foreground">{label}</p>
        {sub && (
          <p className="mt-0.5 text-[13px] text-muted-foreground">{sub}</p>
        )}
      </div>
      {actions && <div className="shrink-0 flex items-center gap-2">{actions}</div>}
    </div>
  );
}

export function UsageSection() {
  const { data, isLoading } = useSettings();

  if (isLoading || !data) {
    return <p className="text-sm text-muted-foreground">Loading…</p>;
  }

  const configuredProviders = Object.entries(data.providers).filter(
    ([, v]) => (v as { hasKey: boolean }).hasKey,
  );

  return (
    <div className="space-y-8">
      {/* Plan usage limits */}
      <section>
        <SectionHeader title="API usage limits" right="Unlimited" />
        <BarRow
          label="Current session"
          labelRight="0% used"
          sub="Resets on page reload"
          percent={0}
        />
      </section>

      {/* Provider limits */}
      <section>
        <SectionHeader
          title="Provider limits"
          sub={
            <button
              type="button"
              onClick={() =>
                toast.message(
                  "Provider rate limits are set by each API provider.",
                )
              }
              className="text-[13px] text-primary hover:underline"
            >
              Learn more about provider limits
            </button>
          }
        />

        {configuredProviders.length === 0 ? (
          <div className="py-4">
            <p className="text-[13px] text-muted-foreground">
              No API keys configured.{" "}
              <span className="font-medium text-foreground">Providers</span> tab
              to add keys.
            </p>
          </div>
        ) : (
          configuredProviders.map(([provider]) => (
            <BarRow
              key={provider}
              label={provider.charAt(0).toUpperCase() + provider.slice(1)}
              labelRight="0% used"
              sub="Rate limits set by provider"
              percent={0}
            />
          ))
        )}

        <div className="flex items-center gap-1.5 pb-2 pt-1">
          <span className="text-[13px] text-muted-foreground">
            Last updated: just now
          </span>
          <RotateCcw className="size-3 text-muted-foreground/60" />
        </div>
      </section>

      {/* Additional features */}
      <section>
        <SectionHeader title="Additional features" />
        <BarRow
          label="Daily workspace operations"
          labelRight="0 / 15"
          sub="You haven't run any operations yet"
          percent={0}
          info
        />
      </section>

      {/* Cost tracking — needs real per-turn usage instrumentation, not yet
          wired. Shown honestly as "Coming soon" with inert controls rather
          than buttons that fake a "coming soon" toast on click. */}
      <section>
        <SectionHeader title="Cost tracking" right="Coming soon" />

        <div className="flex items-center justify-between gap-4 py-3.5">
          <p className="text-[13px] text-muted-foreground">
            Cost alerts notify you when you approach your budget limit — coming
            once per-turn usage metering is wired.
          </p>
          <Switch checked={false} disabled />
        </div>

        <div className="border-t border-border/60">
          <BarRow
            label="$0.00 spent"
            labelRight="0% used"
            sub="Resets monthly"
            percent={0}
          />
        </div>

        <div className="border-t border-border/60">
          <AmountRow
            amount="No limit set"
            label="Monthly API budget"
            info
            actions={
              <Button variant="outline" size="sm" disabled>
                Set limit
              </Button>
            }
          />
        </div>

        <div className="border-t border-border/60">
          <AmountRow
            amount="Pay-as-you-go"
            label="Billing model"
            sub="Costs billed directly by each provider"
            actions={
              <Button variant="outline" size="sm" disabled>
                View providers
              </Button>
            }
          />
        </div>
      </section>
    </div>
  );
}
