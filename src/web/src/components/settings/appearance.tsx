"use client";

import { toast } from "sonner";
import { cn } from "@/lib/utils";
import { SettingsSection, Field } from "./field";
import { useSettings, useUpdateSettings } from "@/hooks/use-settings";

const FONT_SIZES: Array<{ id: "sm" | "md" | "lg"; label: string }> = [
  { id: "sm", label: "Small" },
  { id: "md", label: "Medium" },
  { id: "lg", label: "Large" },
];

const DENSITIES: Array<{ id: "compact" | "cozy"; label: string }> = [
  { id: "compact", label: "Compact" },
  { id: "cozy", label: "Cozy" },
];

export function AppearanceSection() {
  const { data } = useSettings();
  const update = useUpdateSettings();

  if (!data) return <p className="text-sm text-muted-foreground">Loading…</p>;

  const apply = async (patch: Partial<typeof data.appearance>) => {
    try {
      await update.mutateAsync({ appearance: patch });
      toast.success("Appearance updated");
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Failed");
    }
  };

  return (
    <SettingsSection
      title="Theme"
      description="Jarvis is dark-only for now. Tweak density and size to taste."
    >
      <Field label="Font size">
        <div className="flex gap-2">
          {FONT_SIZES.map((f) => (
            <button
              key={f.id}
              onClick={() => apply({ fontSize: f.id })}
              className={cn(
                "flex-1 rounded-md border px-3 py-2 text-sm transition-colors",
                data.appearance.fontSize === f.id
                  ? "border-primary/60 bg-primary/15 text-foreground"
                  : "border-border/60 bg-background/40 text-muted-foreground hover:border-border hover:text-foreground",
              )}
            >
              {f.label}
            </button>
          ))}
        </div>
      </Field>

      <Field label="Density">
        <div className="flex gap-2">
          {DENSITIES.map((d) => (
            <button
              key={d.id}
              onClick={() => apply({ density: d.id })}
              className={cn(
                "flex-1 rounded-md border px-3 py-2 text-sm transition-colors",
                data.appearance.density === d.id
                  ? "border-primary/60 bg-primary/15 text-foreground"
                  : "border-border/60 bg-background/40 text-muted-foreground hover:border-border hover:text-foreground",
              )}
            >
              {d.label}
            </button>
          ))}
        </div>
      </Field>
    </SettingsSection>
  );
}
