import { cn } from "@/lib/utils";

export function SettingsSection({
  title,
  description,
  children,
  className,
}: {
  title?: string;
  description?: string;
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <section className={cn("mb-8", className)}>
      {(title || description) && (
        <div className="mb-3">
          {title && (
            <h2 className="text-sm font-semibold tracking-tight">{title}</h2>
          )}
          {description && (
            <p className="mt-1 text-xs leading-5 text-muted-foreground">
              {description}
            </p>
          )}
        </div>
      )}
      <div className="space-y-4 rounded-lg border border-border/70 bg-card/40 p-4">
        {children}
      </div>
    </section>
  );
}

export function Field({
  label,
  hint,
  children,
  action,
}: {
  label: string;
  hint?: string;
  children: React.ReactNode;
  action?: React.ReactNode;
}) {
  return (
    <div className="space-y-1.5">
      <div className="flex items-center justify-between gap-2">
        <label className="text-sm font-medium text-foreground">{label}</label>
        {action}
      </div>
      {children}
      {hint && (
        <p className="text-[11px] leading-4 text-muted-foreground">{hint}</p>
      )}
    </div>
  );
}

export function SavedIndicator({ state }: { state: "idle" | "saving" | "saved" | "error" }) {
  if (state === "idle") return null;
  const map = {
    saving: { label: "saving…", dot: "bg-muted-foreground" },
    saved: { label: "saved", dot: "bg-primary" },
    error: { label: "error", dot: "bg-destructive" },
  } as const;
  const cfg = map[state];
  return (
    <span className="flex items-center gap-1.5 font-mono text-[10px] uppercase tracking-wider text-muted-foreground">
      <span className={cn("size-1.5 rounded-full", cfg.dot)} />
      {cfg.label}
    </span>
  );
}
