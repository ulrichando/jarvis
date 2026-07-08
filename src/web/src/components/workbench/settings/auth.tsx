"use client";

import { useEffect, useState } from "react";
import { useQueryClient, useMutation } from "@tanstack/react-query";
import { Lock } from "lucide-react";
import { toast } from "sonner";
import { cn } from "@/lib/utils";
import { type WorkspaceMeta, patchWorkspace, Section } from "./shared";

// ── Authentication ────────────────────────────────────────────────────

export const AUTH_PROVIDERS = [
  { id: "credentials", label: "Email + Password", needsEnv: [] },
  { id: "magic-link", label: "Magic Link (Email)", needsEnv: ["SMTP_HOST", "SMTP_USER", "SMTP_PASSWORD", "SMTP_FROM"] },
  { id: "google", label: "Google OAuth", needsEnv: ["GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET"] },
  { id: "github", label: "GitHub OAuth", needsEnv: ["GITHUB_CLIENT_ID", "GITHUB_CLIENT_SECRET"] },
] as const;

export type AuthProvider = "credentials" | "magic-link" | "google" | "github";

export function AuthSection({
  ws,
  workspaceId,
  onChanged,
}: {
  ws: WorkspaceMeta | null;
  workspaceId: string;
  onChanged: () => void;
}) {
  const qc = useQueryClient();
  const auth = (ws as WorkspaceMeta & { auth?: {
    providers: AuthProvider[];
    sessionMins: number;
    cookieSecure: boolean;
    cookieSameSite: "lax" | "strict" | "none";
    scaffolded?: boolean;
  } })?.auth ?? {
    providers: [] as AuthProvider[],
    sessionMins: 1440,
    cookieSecure: false,
    cookieSameSite: "lax" as const,
    scaffolded: false,
  };
  const [providers, setProviders] = useState<AuthProvider[]>(auth.providers);
  const [sessionMins, setSessionMins] = useState(auth.sessionMins);
  const [cookieSecure, setCookieSecure] = useState(auth.cookieSecure);
  const [cookieSameSite, setCookieSameSite] = useState(auth.cookieSameSite);

  // Re-sync local state when ws.auth changes from outside.
  useEffect(() => {
    if (!ws) return;
    const a = (ws as WorkspaceMeta & { auth?: typeof auth }).auth;
    if (!a) return;
    setProviders(a.providers);
    setSessionMins(a.sessionMins);
    setCookieSecure(a.cookieSecure);
    setCookieSameSite(a.cookieSameSite);
  }, [ws]);

  const save = useMutation({
    mutationFn: () =>
      patchWorkspace(workspaceId, {
        auth: {
          providers,
          sessionMins,
          cookieSecure,
          cookieSameSite,
        },
      }),
    onSuccess: () => {
      toast.success("Auth config saved");
      onChanged();
    },
    onError: (err: Error) => toast.error(`Save failed: ${err.message}`),
  });

  const scaffold = useMutation({
    mutationFn: async () => {
      const r = await fetch(
        `/api/workspace/${workspaceId}/auth/scaffold`,
        { method: "POST" },
      );
      const j = await r.json();
      if (!r.ok) throw new Error(j.hint ?? j.error ?? r.statusText);
      return j;
    },
    onSuccess: (j: { written: string[]; deps: string[]; hint: string }) => {
      toast.success(`Scaffolded ${j.written.length} files`, {
        description: j.hint,
        duration: 8000,
      });
      qc.invalidateQueries({ queryKey: ["ws", workspaceId, "meta"] });
    },
    onError: (err: Error) => toast.error(`Scaffold failed: ${err.message}`),
  });

  const toggle = (p: AuthProvider) => {
    setProviders((cur) =>
      cur.includes(p) ? cur.filter((x) => x !== p) : [...cur, p],
    );
  };

  const envSet = new Set(Object.keys(ws?.envVars ?? {}));

  return (
    <>
      <Section
        title="Providers"
        icon={<Lock className="size-3.5" />}
        hint="Select which sign-in methods your deployed app should support. Scaffold writes Auth.js (next-auth v5) boilerplate into the workspace."
      >
        <div className="space-y-2">
          {AUTH_PROVIDERS.map((p) => {
            const enabled = providers.includes(p.id);
            const missingEnv = p.needsEnv.filter((e) => !envSet.has(e));
            return (
              <div
                key={p.id}
                className={cn(
                  "rounded-md border px-3 py-2",
                  enabled ? "border-primary/40 bg-primary/5" : "border-border/40",
                )}
              >
                <label className="flex items-center gap-2 cursor-pointer">
                  <input
                    type="checkbox"
                    checked={enabled}
                    onChange={() => toggle(p.id)}
                    className="accent-primary"
                  />
                  <span className="flex-1 text-[12.5px] font-medium">
                    {p.label}
                  </span>
                </label>
                {enabled && p.needsEnv.length > 0 && (
                  <div className="mt-1.5 ml-6 text-[11px]">
                    <span className="text-muted-foreground">
                      Required env vars:
                    </span>
                    <span className="ml-1.5 font-mono">
                      {p.needsEnv.map((e, i) => (
                        <span
                          key={e}
                          className={
                            envSet.has(e)
                              ? "text-emerald-500/85"
                              : "text-amber-500/85"
                          }
                        >
                          {e}
                          {i < p.needsEnv.length - 1 && ", "}
                        </span>
                      ))}
                    </span>
                    {missingEnv.length > 0 && (
                      <p className="mt-0.5 text-amber-500/85">
                        Add the missing vars in Settings → Secrets before
                        scaffolding.
                      </p>
                    )}
                  </div>
                )}
              </div>
            );
          })}
        </div>
      </Section>

      <Section title="Session config">
        <div className="space-y-3">
          <div>
            <label className="block text-[11px] uppercase tracking-wider text-muted-foreground mb-1">
              Session lifetime (minutes)
            </label>
            <input
              type="number"
              min={5}
              max={43200}
              value={sessionMins}
              onChange={(e) => setSessionMins(parseInt(e.target.value, 10) || 1440)}
              className="w-32 rounded-md border border-border bg-background px-2 py-1 text-[12px] outline-none focus:border-primary"
            />
            <p className="mt-1 text-[11px] text-muted-foreground">
              5 to 43200 (30 days). Default 1440 (24h).
            </p>
          </div>
          <div>
            <label className="block text-[11px] uppercase tracking-wider text-muted-foreground mb-1">
              SameSite cookie attribute
            </label>
            <div className="flex gap-2">
              {(["lax", "strict", "none"] as const).map((s) => (
                <button
                  key={s}
                  type="button"
                  onClick={() => setCookieSameSite(s)}
                  className={cn(
                    "rounded-md border px-3 py-1 text-[11.5px]",
                    cookieSameSite === s
                      ? "border-primary/60 bg-primary/15 text-primary"
                      : "border-border/60 hover:bg-accent",
                  )}
                >
                  {s}
                </button>
              ))}
            </div>
          </div>
          <label className="flex items-center gap-2 cursor-pointer">
            <input
              type="checkbox"
              checked={cookieSecure}
              onChange={(e) => setCookieSecure(e.target.checked)}
              className="accent-primary"
            />
            <span className="text-[12px]">
              Cookie <code className="font-mono">Secure</code> flag
            </span>
            <span className="text-[11px] text-muted-foreground">
              (production = on; localhost = off)
            </span>
          </label>
        </div>
      </Section>

      <Section title="Apply">
        <div className="flex flex-wrap items-center gap-2">
          <button
            type="button"
            onClick={() => save.mutate()}
            disabled={save.isPending}
            className="rounded-md border border-border px-3 py-1.5 text-[11.5px] hover:bg-accent disabled:opacity-50"
          >
            {save.isPending ? "Saving…" : "Save config"}
          </button>
          <button
            type="button"
            onClick={() => scaffold.mutate()}
            disabled={scaffold.isPending || providers.length === 0}
            className="rounded-md border border-primary/50 bg-primary/10 px-3 py-1.5 text-[11.5px] text-primary hover:bg-primary/15 disabled:opacity-50"
            title={
              providers.length === 0
                ? "Enable at least one provider first"
                : "Write next-auth boilerplate into the workspace"
            }
          >
            {scaffold.isPending
              ? "Scaffolding…"
              : auth.scaffolded
                ? "Re-scaffold"
                : "Scaffold Auth files"}
          </button>
          {auth.scaffolded && (
            <span className="rounded bg-emerald-500/15 px-1.5 py-0.5 text-[10px] uppercase tracking-wider text-emerald-400">
              scaffolded
            </span>
          )}
        </div>
        <p className="mt-2 text-[11.5px] text-muted-foreground">
          Scaffold writes <code className="font-mono">auth.ts</code>,{" "}
          <code className="font-mono">app/api/auth/[...nextauth]/route.ts</code>,{" "}
          <code className="font-mono">middleware.ts</code>, and{" "}
          <code className="font-mono">lib/db/users.ts</code>. After scaffolding,
          run <code className="font-mono">bun install next-auth@beta @auth/core</code>{" "}
          (the AI can do this in chat) and add OAuth env vars in Secrets.
        </p>
      </Section>
    </>
  );
}
