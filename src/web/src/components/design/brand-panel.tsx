"use client";

import { useEffect, useState } from "react";
import { Loader2, Upload } from "lucide-react";
import { Button } from "@/components/ui/button";
import { useBrand, usePutBrand } from "@/hooks/use-brand";
import type { Brand } from "@/lib/design/brand";

const EMPTY: Brand = {
  version: 1,
  name: "",
  colors: {
    bg: "#0B0B0F",
    fg: "#F4F4F5",
    accent: "#FF6A00",
    muted: "#71717A",
    supporting: "#27272A",
  },
  fonts: {
    display: { family: "Bricolage Grotesque" },
    body: { family: "IBM Plex Sans" },
  },
};

function readFileAsDataUrl(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(reader.result as string);
    reader.onerror = () => reject(reader.error);
    reader.readAsDataURL(file);
  });
}

export function BrandPanel({ workspaceId }: { workspaceId: string }) {
  const { data: existing, isLoading } = useBrand(workspaceId);
  const put = usePutBrand(workspaceId);
  const [draft, setDraft] = useState<Brand>(EMPTY);
  const [logoFile, setLogoFile] = useState<{ name: string; base64: string } | null>(null);

  useEffect(() => {
    if (existing) setDraft(existing);
  }, [existing]);

  if (isLoading) {
    return (
      <div className="flex h-full items-center justify-center text-[13px] text-muted-foreground">
        loading…
      </div>
    );
  }

  const onLogo = async (file: File) => {
    const dataUrl = await readFileAsDataUrl(file);
    const base64 = dataUrl.split(",")[1] ?? "";
    setLogoFile({ name: file.name, base64 });
  };

  const save = () => {
    put.mutate({
      brand: draft,
      logoBase64: logoFile?.base64,
      logoFilename: logoFile?.name,
    });
  };

  return (
    <div className="flex h-full flex-col gap-4 overflow-y-auto p-5">
      <Field label="Brand name">
        <input
          className="w-full rounded-md border border-border/60 bg-background px-3 py-1.5 text-[13px]"
          value={draft.name}
          onChange={(e) => setDraft({ ...draft, name: e.target.value })}
          placeholder="Pretva"
        />
      </Field>

      <Field label="Logo">
        <label className="flex cursor-pointer items-center gap-2 rounded-md border border-dashed border-border/60 px-3 py-3 text-[13px] text-muted-foreground hover:bg-muted/30">
          <Upload className="size-4" />
          {logoFile?.name ?? draft.logoPath ?? "Upload logo (PNG/SVG, ≤2MB)"}
          <input
            type="file"
            accept="image/png,image/svg+xml,image/jpeg"
            className="hidden"
            onChange={(e) => e.target.files?.[0] && onLogo(e.target.files[0])}
          />
        </label>
      </Field>

      <Field label="Colors">
        <div className="grid grid-cols-5 gap-2">
          {(["bg", "fg", "accent", "muted", "supporting"] as const).map((k) => (
            <label key={k} className="flex flex-col gap-1 text-[11px] text-muted-foreground">
              <span className="uppercase tracking-wide">{k}</span>
              <input
                type="color"
                value={draft.colors[k]}
                onChange={(e) =>
                  setDraft({ ...draft, colors: { ...draft.colors, [k]: e.target.value } })
                }
                className="h-8 w-full cursor-pointer rounded-md border border-border/60 bg-background"
              />
            </label>
          ))}
        </div>
      </Field>

      <Field label="Fonts (Google Fonts family names)">
        <div className="grid grid-cols-2 gap-2">
          <input
            className="rounded-md border border-border/60 bg-background px-3 py-1.5 text-[13px]"
            placeholder="Display (e.g. Bricolage Grotesque)"
            value={draft.fonts.display.family}
            onChange={(e) =>
              setDraft({
                ...draft,
                fonts: { ...draft.fonts, display: { family: e.target.value } },
              })
            }
          />
          <input
            className="rounded-md border border-border/60 bg-background px-3 py-1.5 text-[13px]"
            placeholder="Body (e.g. IBM Plex Sans)"
            value={draft.fonts.body.family}
            onChange={(e) =>
              setDraft({
                ...draft,
                fonts: { ...draft.fonts, body: { family: e.target.value } },
              })
            }
          />
        </div>
      </Field>

      <Field label="Voice (optional)">
        <textarea
          rows={3}
          className="w-full rounded-md border border-border/60 bg-background px-3 py-1.5 text-[13px]"
          placeholder="Confident, concise, founder-direct. Avoid jargon."
          value={draft.voice ?? ""}
          onChange={(e) => setDraft({ ...draft, voice: e.target.value })}
        />
      </Field>

      <div className="flex items-center gap-2">
        <Button size="sm" onClick={save} disabled={put.isPending || !draft.name}>
          {put.isPending && <Loader2 className="size-3.5 animate-spin" />}
          Save brand
        </Button>
        {put.isSuccess && <span className="text-[12px] text-muted-foreground">Saved.</span>}
        {put.isError && <span className="text-[12px] text-red-500">Failed to save.</span>}
      </div>
    </div>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex flex-col gap-1.5">
      <span className="text-[11px] font-medium uppercase tracking-wide text-muted-foreground">
        {label}
      </span>
      {children}
    </div>
  );
}
