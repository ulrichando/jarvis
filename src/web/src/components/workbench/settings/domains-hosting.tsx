"use client";

import { useState } from "react";
import { useQuery, useQueryClient, useMutation } from "@tanstack/react-query";
import { Globe, Loader2, X } from "lucide-react";
import { toast } from "sonner";
import { cn } from "@/lib/utils";
import { type WorkspaceMeta, Section, KvRow, relativeTime } from "./shared";

// ── Domains & Hosting (Vercel) ─────────────────────────────────────────

export type DeploymentRow = {
  uid: string;
  url: string;
  state: string;
  createdAt: number;
  target?: string | null;
  inspectorUrl?: string;
};

export type DeploymentsResponse = {
  provider: "vercel" | null;
  configured: boolean;
  deployments: DeploymentRow[];
  hint?: string;
  error?: string;
};

export type DomainRow = {
  name: string;
  verified: boolean;
  verification?: Array<{ type: string; domain: string; value: string }>;
};

export type DomainsResponse = {
  configured: boolean;
  domains: DomainRow[];
  error?: string;
};

export async function fetchDeployments(id: string): Promise<DeploymentsResponse> {
  const r = await fetch(`/api/workspace/${id}/deploy`);
  return r.json();
}

export async function postDeploy(
  id: string,
  target: "production" | "preview" = "production",
): Promise<{ deployment?: DeploymentRow; error?: string; message?: string }> {
  const r = await fetch(`/api/workspace/${id}/deploy`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ target }),
  });
  return r.json();
}

export async function fetchDomainsList(id: string): Promise<DomainsResponse> {
  const r = await fetch(`/api/workspace/${id}/domains`);
  return r.json();
}

export async function addDomainReq(id: string, domain: string) {
  const r = await fetch(`/api/workspace/${id}/domains`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ domain }),
  });
  if (!r.ok) {
    const j = await r.json().catch(() => ({}));
    throw new Error(j.message ?? j.error ?? r.statusText);
  }
  return r.json();
}

export async function removeDomainReq(id: string, domain: string) {
  const r = await fetch(
    `/api/workspace/${id}/domains?domain=${encodeURIComponent(domain)}`,
    { method: "DELETE" },
  );
  if (!r.ok) {
    const j = await r.json().catch(() => ({}));
    throw new Error(j.message ?? j.error ?? r.statusText);
  }
  return r.json();
}

export function DomainsHostingSection({
  ws,
  workspaceId,
  onChanged,
  onSwitchToSecrets,
}: {
  ws: WorkspaceMeta | null;
  workspaceId: string;
  onChanged: () => void;
  onSwitchToSecrets: () => void;
}) {
  const qc = useQueryClient();
  const hasToken =
    ws?.envVars && Object.keys(ws.envVars).includes("VERCEL_TOKEN");

  const { data: dep } = useQuery({
    queryKey: ["ws", workspaceId, "deploy"],
    queryFn: () => fetchDeployments(workspaceId),
    refetchInterval: 15000,
    enabled: !!hasToken,
  });

  const { data: domsData } = useQuery({
    queryKey: ["ws", workspaceId, "domains"],
    queryFn: () => fetchDomainsList(workspaceId),
    refetchInterval: 15000,
    enabled: !!hasToken && dep?.configured === true,
  });

  const deploy = useMutation({
    mutationFn: (target: "production" | "preview") =>
      postDeploy(workspaceId, target),
    onSuccess: (r) => {
      if (r.deployment) {
        toast.success("Deploy started", {
          description: r.deployment.url,
        });
        qc.invalidateQueries({ queryKey: ["ws", workspaceId, "deploy"] });
        qc.invalidateQueries({ queryKey: ["ws", workspaceId, "meta"] });
        onChanged();
      } else if (r.error) {
        toast.error(`Deploy failed: ${r.message ?? r.error}`);
      }
    },
    onError: (err: Error) => toast.error(`Deploy failed: ${err.message}`),
  });

  const addDom = useMutation({
    mutationFn: (domain: string) => addDomainReq(workspaceId, domain),
    onSuccess: () => {
      toast.success("Domain added — verify the DNS records to activate");
      qc.invalidateQueries({ queryKey: ["ws", workspaceId, "domains"] });
    },
    onError: (err: Error) => toast.error(`Add failed: ${err.message}`),
  });

  const remDom = useMutation({
    mutationFn: (domain: string) => removeDomainReq(workspaceId, domain),
    onSuccess: () => {
      toast.success("Domain removed");
      qc.invalidateQueries({ queryKey: ["ws", workspaceId, "domains"] });
    },
    onError: (err: Error) => toast.error(`Remove failed: ${err.message}`),
  });

  const [newDomain, setNewDomain] = useState("");

  // ── Render ────────────────────────────────────────────────────────────

  if (!hasToken) {
    return (
      <Section
        title="Domains & Hosting"
        icon={<Globe className="size-3.5" />}
        hint="Vercel is the first deploy target. Add VERCEL_TOKEN to Secrets to enable."
      >
        <div className="rounded-md border border-amber-500/30 bg-amber-500/5 p-4">
          <h4 className="text-[13px] font-medium">Connect Vercel</h4>
          <ol className="mt-2 space-y-1.5 text-[12px] text-foreground/85">
            <li>
              1. Open{" "}
              <a
                href="https://vercel.com/account/tokens"
                target="_blank"
                rel="noreferrer"
                className="text-primary underline"
              >
                vercel.com/account/tokens
              </a>{" "}
              and create a token with <code className="font-mono">Full Account</code> scope.
            </li>
            <li>
              2. Copy the token, then go to{" "}
              <button
                type="button"
                onClick={onSwitchToSecrets}
                className="text-primary underline"
              >
                Secrets
              </button>{" "}
              and add it as <code className="font-mono">VERCEL_TOKEN</code>.
            </li>
            <li>3. Come back here — Deploy + Domains will light up.</li>
          </ol>
          <p className="mt-3 text-[11.5px] text-muted-foreground">
            Multi-provider support (Netlify, Cloudflare Pages, Fly.io,
            self-hosted) is on the roadmap.
          </p>
        </div>
      </Section>
    );
  }

  const productionUrl = ws?.deploy?.productionUrl
    ? `https://${ws.deploy.productionUrl}`
    : null;
  const latestState = dep?.deployments?.[0]?.state;

  return (
    <>
      <Section
        title="Production"
        icon={<Globe className="size-3.5" />}
        hint={`Deploys this workspace to Vercel${ws?.deploy?.projectName ? ` as project "${ws.deploy.projectName}"` : ""}.`}
      >
        <div className="grid grid-cols-2 gap-x-6 gap-y-1.5 text-[12px]">
          <KvRow label="Provider">
            <span className="font-mono">vercel</span>
          </KvRow>
          <KvRow label="Project">
            <span className="font-mono">
              {ws?.deploy?.projectName ?? "(creates on first deploy)"}
            </span>
          </KvRow>
          <KvRow label="Production URL">
            {productionUrl ? (
              <a
                href={productionUrl}
                target="_blank"
                rel="noreferrer"
                className="font-mono text-primary hover:underline"
              >
                {ws?.deploy?.productionUrl}
              </a>
            ) : (
              <span className="text-muted-foreground">—</span>
            )}
          </KvRow>
          <KvRow label="Latest state">
            {latestState ? (
              <span
                className={cn(
                  "rounded px-1.5 py-0.5 text-[10px] uppercase tracking-wider",
                  latestState === "READY"
                    ? "bg-emerald-500/15 text-emerald-400"
                    : latestState === "ERROR" || latestState === "CANCELED"
                      ? "bg-destructive/15 text-destructive"
                      : "bg-amber-500/15 text-amber-400",
                )}
              >
                {latestState.toLowerCase()}
              </span>
            ) : (
              <span className="text-muted-foreground">—</span>
            )}
          </KvRow>
        </div>
        <div className="mt-3 flex flex-wrap gap-2">
          <button
            type="button"
            onClick={() => deploy.mutate("production")}
            disabled={deploy.isPending}
            className="flex items-center gap-1.5 rounded-md border border-primary/50 bg-primary/10 px-3 py-1.5 text-[12px] text-primary hover:bg-primary/15 disabled:opacity-50"
          >
            {deploy.isPending ? (
              <Loader2 className="size-3.5 animate-spin" />
            ) : (
              <Globe className="size-3.5" />
            )}
            Deploy to production
          </button>
          <button
            type="button"
            onClick={() => deploy.mutate("preview")}
            disabled={deploy.isPending}
            className="flex items-center gap-1.5 rounded-md border border-border/60 px-3 py-1.5 text-[12px] hover:bg-accent disabled:opacity-50"
          >
            Deploy preview
          </button>
        </div>
        {dep?.error && (
          <p className="mt-2 text-[11.5px] text-destructive/85">
            Vercel API: {dep.error}
          </p>
        )}
      </Section>

      <Section title="Recent deployments">
        {!dep?.deployments?.length ? (
          <p className="text-[12px] text-muted-foreground">
            No deployments yet.
          </p>
        ) : (
          <div className="space-y-1">
            {dep.deployments.map((d) => (
              <a
                key={d.uid}
                href={`https://${d.url}`}
                target="_blank"
                rel="noreferrer"
                className="flex items-center gap-3 rounded-md border border-border/40 px-3 py-2 text-[12px] hover:bg-accent/40"
              >
                <span
                  className={cn(
                    "shrink-0 rounded px-1.5 py-0 text-[10px] uppercase tracking-wider",
                    d.state === "READY"
                      ? "bg-emerald-500/15 text-emerald-400"
                      : d.state === "ERROR" || d.state === "CANCELED"
                        ? "bg-destructive/15 text-destructive"
                        : "bg-amber-500/15 text-amber-400",
                  )}
                >
                  {d.state.toLowerCase()}
                </span>
                <span className="flex-1 truncate font-mono">{d.url}</span>
                <span className="shrink-0 text-[10.5px] uppercase tracking-wider text-muted-foreground/70">
                  {d.target ?? "production"}
                </span>
                <span className="shrink-0 text-[11px] text-muted-foreground">
                  {relativeTime(d.createdAt)}
                </span>
              </a>
            ))}
          </div>
        )}
      </Section>

      <Section
        title="Custom domains"
        hint="Domain verification happens via DNS — Vercel returns the records you need to add."
      >
        {!dep?.configured ? (
          <p className="text-[12px] text-muted-foreground">
            Run a first deploy to initialize the project, then add domains.
          </p>
        ) : (
          <>
            <div className="space-y-1">
              {(domsData?.domains ?? []).length === 0 ? (
                <p className="text-[12px] text-muted-foreground">
                  No custom domains yet — using the generated{" "}
                  <code className="font-mono">.vercel.app</code> URL.
                </p>
              ) : (
                domsData!.domains.map((d) => (
                  <div
                    key={d.name}
                    className="rounded-md border border-border/40 px-3 py-2 text-[12px]"
                  >
                    <div className="flex items-center gap-3">
                      <span className="flex-1 truncate font-mono">
                        {d.name}
                      </span>
                      <span
                        className={cn(
                          "rounded px-1.5 py-0 text-[10px] uppercase tracking-wider",
                          d.verified
                            ? "bg-emerald-500/15 text-emerald-400"
                            : "bg-amber-500/15 text-amber-400",
                        )}
                      >
                        {d.verified ? "verified" : "pending DNS"}
                      </span>
                      <button
                        type="button"
                        onClick={() => {
                          if (confirm(`Remove ${d.name}?`))
                            remDom.mutate(d.name);
                        }}
                        disabled={remDom.isPending}
                        className="rounded p-1 text-muted-foreground hover:bg-destructive/10 hover:text-destructive disabled:opacity-50"
                        aria-label="Remove domain"
                      >
                        <X className="size-3.5" />
                      </button>
                    </div>
                    {!d.verified &&
                      d.verification &&
                      d.verification.length > 0 && (
                        <div className="mt-2 rounded bg-muted/40 p-2 font-mono text-[10.5px]">
                          <div className="mb-1 text-[9.5px] uppercase tracking-wider text-muted-foreground/80">
                            Add this DNS record
                          </div>
                          {d.verification.slice(0, 1).map((v, i) => (
                            <div key={i}>
                              {v.type} · {v.domain} · {v.value}
                            </div>
                          ))}
                        </div>
                      )}
                  </div>
                ))
              )}
            </div>
            <div className="mt-3 flex items-center gap-2">
              <input
                value={newDomain}
                onChange={(e) => setNewDomain(e.target.value)}
                placeholder="example.com"
                className="flex-1 rounded-md border border-border bg-background px-3 py-1.5 font-mono text-[12px] outline-none focus:border-primary"
              />
              <button
                type="button"
                onClick={() => {
                  const d = newDomain.trim().toLowerCase();
                  if (!d) return;
                  // Fail fast in the UI instead of round-tripping to the
                  // server's stricter validator (matches it: per-label,
                  // alphabetic TLD, no leading/trailing hyphen).
                  const ok =
                    d.length <= 253 &&
                    d.split(".").length >= 2 &&
                    /^[a-z]{2,}$/.test(d.split(".").pop() ?? "") &&
                    d
                      .split(".")
                      .every(
                        (l) =>
                          /^[a-z0-9-]{1,63}$/.test(l) &&
                          !l.startsWith("-") &&
                          !l.endsWith("-"),
                      );
                  if (!ok) {
                    toast.error("Invalid domain", {
                      description: "Enter a valid hostname, e.g. example.com",
                    });
                    return;
                  }
                  addDom.mutate(d);
                  setNewDomain("");
                }}
                disabled={addDom.isPending || !newDomain.trim()}
                className="rounded-md border border-border px-3 py-1.5 text-[11.5px] hover:bg-accent disabled:opacity-50"
              >
                Add
              </button>
            </div>
            {domsData?.error && (
              <p className="mt-2 text-[11.5px] text-destructive/85">
                Vercel API: {domsData.error}
              </p>
            )}
          </>
        )}
      </Section>
    </>
  );
}
