import type { LucideIcon } from "lucide-react";

export function Placeholder({
  icon: Icon,
  title,
  description,
}: {
  icon: LucideIcon;
  title: string;
  description: string;
}) {
  return (
    <div className="flex h-full flex-col items-center justify-center px-6 text-center">
      <div className="flex size-14 items-center justify-center rounded-xl border border-primary/30 bg-primary/10">
        <Icon className="size-6 text-primary" />
      </div>
      <h1 className="mt-5 text-2xl font-semibold tracking-tight">{title}</h1>
      <p className="mt-2 max-w-md text-sm text-muted-foreground">{description}</p>
      <div className="mt-6 inline-flex items-center gap-2 rounded-full border border-border/60 bg-card/60 px-3 py-1 font-mono text-[10px] uppercase tracking-[0.18em] text-muted-foreground">
        <span className="size-1.5 rounded-full bg-primary" />
        coming online soon
      </div>
    </div>
  );
}
