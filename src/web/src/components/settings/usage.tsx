"use client";

import { useState } from "react";
import { AlertTriangle, RotateCcw } from "lucide-react";
import { useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { Switch } from "@/components/ui/switch";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { cn } from "@/lib/utils";
import { useSettings, useUpdateSettings, useUsage } from "@/hooks/use-settings";

function SectionHeader({
  title,
  right,
  sub,
}: {
  title: string;
  right?: React.ReactNode;
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
}: {
  label: string;
  labelRight: string;
  sub: string;
  percent: number;
}) {
  return (
    <div className="py-4">
      <div className="flex items-baseline justify-between gap-4">
        <span className="text-[14px] font-medium">{label}</span>
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
  actions,
}: {
  amount: string;
  label: string;
  sub?: string;
  actions?: React.ReactNode;
}) {
  return (
    <div className="flex items-center justify-between gap-4 py-4">
      <div>
        <span className="text-[14px] font-semibold">{amount}</span>
        <p className="mt-0.5 text-[13px] text-muted-foreground">{label}</p>
        {sub && (
          <p className="mt-0.5 text-[13px] text-muted-foreground">{sub}</p>
        )}
      </div>
      {actions && (
        <div className="shrink-0 flex items-center gap-2">{actions}</div>
      )}
    </div>
  );
}

function formatTokens(n: number): string {
  if (n < 1000) return String(n);
  if (n < 10_000) return `${(n / 1000).toFixed(1)}k`;
  if (n < 1_000_000) return `${Math.round(n / 1000)}k`;
  return `${(n / 1_000_000).toFixed(1)}M`;
}

function formatCost(cost: number): string {
  if (cost === 0) return "$0.00";
  if (cost < 0.001) return "<$0.001";
  if (cost < 0.01) return `$${cost.toFixed(4)}`;
  if (cost < 1) return `$${cost.toFixed(3)}`;
  return `$${cost.toFixed(2)}`;
}

function usageSub(t: { requests: number; tokensIn: number; tokensOut: number }) {
  if (t.requests === 0) return "No responses yet";
  return `${t.requests} response${t.requests === 1 ? "" : "s"} · ${formatTokens(t.tokensIn)} in · ${formatTokens(t.tokensOut)} out`;
}

/** Budget set/clear dialog — writes settings.usage.monthlyBudgetUsd. */
function BudgetDialog({
  open,
  onOpenChange,
  currentBudget,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  currentBudget: number | null;
}) {
  const update = useUpdateSettings();
  const qc = useQueryClient();
  const [value, setValue] = useState(currentBudget ? String(currentBudget) : "");
  const parsed = Number(value);
  const valid = Number.isFinite(parsed) && parsed > 0 && parsed <= 1_000_000;

  const save = async (budget: number | null) => {
    try {
      await update.mutateAsync({ usage: { monthlyBudgetUsd: budget } });
      // The /api/usage budget block reads the same setting — refresh it.
      qc.invalidateQueries({ queryKey: ["usage"] });
      toast.success(
        budget === null ? "Budget cleared" : `Monthly budget set to $${budget}`,
      );
      onOpenChange(false);
    } catch {
      toast.error("Couldn't save the budget");
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-sm">
        <DialogHeader>
          <DialogTitle>Monthly API budget</DialogTitle>
          <DialogDescription>
            Spend is estimated from list pricing, not provider billing. When a
            budget is set, the Usage page tracks percent used and can warn as
            you approach it.
          </DialogDescription>
        </DialogHeader>
        <div className="flex items-center gap-2">
          <span className="text-[14px] text-muted-foreground">$</span>
          <Input
            type="number"
            min={1}
            max={1_000_000}
            step={1}
            placeholder="25"
            value={value}
            onChange={(e) => setValue(e.target.value)}
            autoFocus
          />
          <span className="text-[13px] text-muted-foreground">/ month</span>
        </div>
        <DialogFooter>
          {currentBudget !== null && (
            <Button
              variant="ghost"
              disabled={update.isPending}
              onClick={() => save(null)}
            >
              Clear limit
            </Button>
          )}
          <Button
            disabled={!valid || update.isPending}
            onClick={() => save(parsed)}
          >
            Save
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

export function UsageSection({
  onOpenSection,
}: {
  /** Jump to a sibling settings section (used by "View providers"). */
  onOpenSection?: (section: "providers") => void;
}) {
  const { data: settings } = useSettings();
  const usage = useUsage();
  const update = useUpdateSettings();
  const qc = useQueryClient();
  const [budgetOpen, setBudgetOpen] = useState(false);

  if (usage.isLoading) {
    return <p className="text-sm text-muted-foreground">Loading…</p>;
  }
  if (usage.isError || !usage.data) {
    return (
      <div className="space-y-2">
        <p className="text-sm text-muted-foreground">
          Couldn&apos;t load usage data.
        </p>
        <Button variant="outline" size="sm" onClick={() => usage.refetch()}>
          Retry
        </Button>
      </div>
    );
  }

  const { today, month, providers, unpricedModels, budget } = usage.data;
  const monthlyBudget = budget.monthlyUsd;
  const percentUsed = budget.percentUsed; // 0..∞ or null
  const alertsEnabled = settings?.usage?.costAlerts ?? budget.alertsEnabled;

  const setAlerts = async (enabled: boolean) => {
    try {
      await update.mutateAsync({ usage: { costAlerts: enabled } });
      qc.invalidateQueries({ queryKey: ["usage"] });
    } catch {
      toast.error("Couldn't update cost alerts");
    }
  };

  const updatedAt = usage.dataUpdatedAt
    ? new Date(usage.dataUpdatedAt).toLocaleTimeString()
    : "—";

  return (
    <div className="space-y-8">
      {/* Totals — estimated from list pricing at read time */}
      <section>
        <SectionHeader title="API usage" right="Pay-as-you-go" />
        <AmountRow
          amount={`${formatCost(today.costUsd)} est.`}
          label="Today"
          sub={usageSub(today)}
        />
        <div className="border-t border-border/60">
          <AmountRow
            amount={`${formatCost(month.costUsd)} est.`}
            label="This month"
            sub={usageSub(month)}
          />
        </div>
      </section>

      {/* Per-provider breakdown (this month) */}
      <section>
        <SectionHeader
          title="Usage by provider"
          sub={
            <span className="text-[13px] text-muted-foreground">
              This month, estimated from public list pricing.
            </span>
          }
        />
        {providers.length === 0 ? (
          <p className="py-4 text-[13px] text-muted-foreground">
            No usage recorded yet this month. Send a chat message and it will
            show up here.
          </p>
        ) : (
          providers.map((p) => (
            <BarRow
              key={p.provider}
              label={p.label}
              labelRight={`${formatCost(p.costUsd)} · ${Math.round(p.share * 100)}%`}
              sub={usageSub(p)}
              percent={p.share * 100}
            />
          ))
        )}
        {unpricedModels.length > 0 && (
          <p className="pb-1 text-[12px] text-muted-foreground/80">
            No list price for {unpricedModels.join(", ")} — their cost is not
            included.
          </p>
        )}
        <div className="flex items-center gap-1.5 pb-2 pt-1">
          <span className="text-[13px] text-muted-foreground">
            Last updated: {updatedAt}
          </span>
          <button
            type="button"
            aria-label="Refresh usage"
            onClick={() => usage.refetch()}
            className="text-muted-foreground/60 hover:text-foreground"
          >
            <RotateCcw
              className={cn("size-3", usage.isFetching && "animate-spin")}
            />
          </button>
        </div>
      </section>

      {/* Cost tracking — real spend vs an optional monthly budget */}
      <section>
        <SectionHeader title="Cost tracking" />

        {alertsEnabled &&
          percentUsed !== null &&
          percentUsed >= 0.8 && (
            <div
              className={cn(
                "mt-3 flex items-start gap-2 rounded-md border px-3 py-2.5 text-[13px]",
                percentUsed >= 1
                  ? "border-destructive/40 bg-destructive/10 text-destructive"
                  : "border-amber-500/40 bg-amber-500/10 text-amber-600 dark:text-amber-400",
              )}
            >
              <AlertTriangle className="mt-0.5 size-3.5 shrink-0" />
              <span>
                {percentUsed >= 1
                  ? `Estimated spend has passed your $${monthlyBudget} monthly budget.`
                  : `Estimated spend is at ${Math.round(percentUsed * 100)}% of your $${monthlyBudget} monthly budget.`}
              </span>
            </div>
          )}

        <div className="flex items-center justify-between gap-4 py-3.5">
          <p className="text-[13px] text-muted-foreground">
            Cost alerts warn you here when estimated spend approaches your
            monthly budget{monthlyBudget === null ? " — set a budget to enable" : ""}.
          </p>
          <Switch
            checked={alertsEnabled}
            disabled={monthlyBudget === null || update.isPending}
            onCheckedChange={setAlerts}
          />
        </div>

        <div className="border-t border-border/60">
          {monthlyBudget !== null && percentUsed !== null ? (
            <BarRow
              label={`${formatCost(month.costUsd)} spent`}
              labelRight={`${Math.round(percentUsed * 100)}% of $${monthlyBudget}`}
              sub="Estimated this month · resets monthly"
              percent={percentUsed * 100}
            />
          ) : (
            <AmountRow
              amount={`${formatCost(month.costUsd)} spent`}
              label="Estimated this month"
              sub="Resets monthly"
            />
          )}
        </div>

        <div className="border-t border-border/60">
          <AmountRow
            amount={monthlyBudget !== null ? `$${monthlyBudget} / month` : "No limit set"}
            label="Monthly API budget"
            actions={
              <Button
                variant="outline"
                size="sm"
                onClick={() => setBudgetOpen(true)}
              >
                {monthlyBudget !== null ? "Change limit" : "Set limit"}
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
              <Button
                variant="outline"
                size="sm"
                onClick={() => onOpenSection?.("providers")}
              >
                View providers
              </Button>
            }
          />
        </div>
      </section>

      {budgetOpen && (
        <BudgetDialog
          open={budgetOpen}
          onOpenChange={setBudgetOpen}
          currentBudget={monthlyBudget}
        />
      )}
    </div>
  );
}
