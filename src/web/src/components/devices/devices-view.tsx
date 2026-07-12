"use client";

/**
 * Devices — the web view over the IoT discovery sidecar (via /api/iot/*).
 *
 * Truthful by design: every device wears its controllability verdict as
 * state, and control buttons are DISABLED with the sidecar's `control_hint`
 * as the visible reason whenever a device isn't locally controllable.
 * Phase 1 is discovery-only — even "local" controls surface the sidecar's
 * real 501 answer instead of pretending to work.
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import {
  ChevronRight,
  CircleHelp,
  Lightbulb,
  Plug,
  Power,
  Radar,
  RefreshCw,
  Router,
  Speaker,
  Thermometer,
  Tv,
  type LucideIcon,
} from "lucide-react";
import { cn } from "@/lib/utils";

export type IotDevice = {
  ip: string;
  mac: string | null;
  hostname: string | null;
  name: string;
  type: string;
  brand: string;
  protocol: string[];
  controllable: "local" | "matter" | "cloud_only" | "unknown";
  control_hint: string;
  first_seen?: number;
  last_seen?: number;
};

/** Registry key, mirroring the sidecar's Device.key (MAC, else ip:<ip>). */
export const deviceKey = (d: IotDevice): string => d.mac ?? `ip:${d.ip}`;

const canControl = (d: IotDevice): boolean =>
  d.controllable === "local" || d.controllable === "matter";

const TYPE_META: Record<string, { label: string; icon: LucideIcon }> = {
  tv: { label: "TVs", icon: Tv },
  speaker: { label: "Speakers", icon: Speaker },
  light: { label: "Lights", icon: Lightbulb },
  hub: { label: "Hubs & bridges", icon: Router },
  plug: { label: "Plugs", icon: Plug },
  thermostat: { label: "Thermostats", icon: Thermometer },
  unknown: { label: "Unidentified", icon: CircleHelp },
};

const typeMeta = (t: string) =>
  TYPE_META[t] ?? { label: t.charAt(0).toUpperCase() + t.slice(1), icon: CircleHelp };

// Canonical section order; anything unmapped lands before "unknown".
const TYPE_ORDER = ["tv", "speaker", "light", "hub", "plug", "thermostat"];

export function groupByType(devices: IotDevice[]): [string, IotDevice[]][] {
  const groups = new Map<string, IotDevice[]>();
  for (const d of devices) {
    const t = d.type || "unknown";
    const list = groups.get(t) ?? [];
    list.push(d);
    groups.set(t, list);
  }
  const rank = (t: string) => {
    const i = TYPE_ORDER.indexOf(t);
    if (i >= 0) return i;
    return t === "unknown" ? TYPE_ORDER.length + 1 : TYPE_ORDER.length;
  };
  return [...groups.entries()]
    .map(([t, list]): [string, IotDevice[]] => [
      t,
      [...list].sort((a, b) => a.name.localeCompare(b.name)),
    ])
    .sort((a, b) => rank(a[0]) - rank(b[0]) || a[0].localeCompare(b[0]));
}

const CONTROL_META: Record<IotDevice["controllable"], { label: string; dot: string }> = {
  local: { label: "Local", dot: "bg-emerald-500" },
  matter: { label: "Matter", dot: "bg-sky-500" },
  cloud_only: { label: "Cloud only", dot: "bg-amber-500" },
  unknown: { label: "Unknown", dot: "bg-muted-foreground/60" },
};

function ControlPill({ level }: { level: IotDevice["controllable"] }) {
  const meta = CONTROL_META[level] ?? CONTROL_META.unknown;
  return (
    <span className="inline-flex shrink-0 items-center gap-1.5 rounded-full border border-border/40 bg-card px-2.5 py-0.5 text-[11px] text-muted-foreground">
      <span className={cn("size-1.5 rounded-full", meta.dot)} />
      {meta.label}
    </span>
  );
}

function DeviceRow({ device }: { device: IotDevice }) {
  const [open, setOpen] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const [sending, setSending] = useState(false);
  const Icon = typeMeta(device.type).icon;
  const controllable = canControl(device);

  const sendPower = useCallback(async () => {
    setSending(true);
    setNotice(null);
    try {
      const res = await fetch(
        `/api/iot/devices/${encodeURIComponent(deviceKey(device))}/command`,
        {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({ action: "power" }),
        },
      );
      const body = (await res.json()) as { error?: string };
      // Phase 1: the sidecar answers 501 — show its real answer, don't fake success.
      setNotice(body.error ?? (res.ok ? "Done" : `HTTP ${res.status}`));
    } catch {
      setNotice("IoT service unreachable");
    } finally {
      setSending(false);
    }
  }, [device]);

  return (
    <li className="group">
      <button
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        className="flex w-full items-center gap-3 rounded-lg px-3 py-2.5 text-left transition-colors hover:bg-muted/50"
      >
        <span className="grid size-8 shrink-0 place-items-center rounded-lg border border-border/40 bg-card text-muted-foreground">
          <Icon className="size-4" />
        </span>
        <span className="min-w-0 flex-1">
          <span className="block truncate text-[13.5px] font-medium text-foreground">
            {device.name || device.hostname || device.ip}
          </span>
          <span className="block truncate text-[11.5px] text-muted-foreground">
            {device.brand || "Unknown make"}
          </span>
        </span>
        <span className="hidden font-mono text-[11px] text-muted-foreground/80 sm:block">
          {device.ip}
        </span>
        <ControlPill level={device.controllable} />
        <ChevronRight
          className={cn(
            "size-3.5 shrink-0 text-muted-foreground/60 transition-transform",
            open && "rotate-90",
          )}
        />
      </button>

      {open && (
        <div className="mx-3 mb-2 rounded-lg border border-border/40 bg-card/40 px-4 py-3">
          <dl className="grid grid-cols-1 gap-x-6 gap-y-1.5 text-[12px] sm:grid-cols-2">
            <Detail label="IP address" value={device.ip} mono />
            {device.mac && <Detail label="MAC" value={device.mac} mono />}
            {device.hostname && <Detail label="Hostname" value={device.hostname} mono />}
            {device.protocol.length > 0 && (
              <Detail label="Seen via" value={device.protocol.join(" · ")} />
            )}
            {device.last_seen ? (
              <Detail
                label="Last seen"
                value={new Date(device.last_seen * 1000).toLocaleString()}
              />
            ) : null}
          </dl>

          <div className="mt-3 flex flex-wrap items-center gap-2 border-t border-border/40 pt-3">
            <button
              aria-label={`Power — ${device.name || device.ip}`}
              disabled={!controllable || sending}
              onClick={controllable ? sendPower : undefined}
              className={cn(
                "inline-flex h-[28px] items-center gap-1.5 rounded-lg border px-2.5 text-[12px] transition-colors",
                controllable
                  ? "border-border/40 bg-card text-foreground hover:border-border"
                  : "cursor-not-allowed border-border/30 bg-card/40 text-muted-foreground/50",
              )}
            >
              <Power className="size-3.5" />
              Power
            </button>
            {!controllable && (
              <span className="text-[11.5px] text-muted-foreground">
                {device.control_hint || "Not locally controllable"}
              </span>
            )}
            {notice && (
              <span role="status" className="text-[11.5px] text-amber-600 dark:text-amber-500">
                {notice}
              </span>
            )}
          </div>
        </div>
      )}
    </li>
  );
}

function Detail({ label, value, mono }: { label: string; value: string; mono?: boolean }) {
  return (
    <div className="flex items-baseline justify-between gap-3 sm:justify-start">
      <dt className="w-24 shrink-0 text-muted-foreground/70">{label}</dt>
      <dd className={cn("truncate text-foreground/90", mono && "font-mono text-[11.5px]")}>
        {value}
      </dd>
    </div>
  );
}

export function DevicesView({ initialDevices }: { initialDevices?: IotDevice[] }) {
  const [devices, setDevices] = useState<IotDevice[]>(initialDevices ?? []);
  const [scanning, setScanning] = useState(false);
  const [loading, setLoading] = useState(initialDevices === undefined);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async (path: string, init?: RequestInit) => {
    try {
      const res = await fetch(path, init);
      const body = (await res.json()) as { devices?: IotDevice[]; error?: string };
      if (!res.ok) {
        setError(body.error ?? `HTTP ${res.status}`);
        return;
      }
      setError(null);
      setDevices(body.devices ?? []);
    } catch {
      setError("Could not reach the web API.");
    }
  }, []);

  // Initial inventory — skipped when the caller seeds the list (tests, SSR).
  useEffect(() => {
    if (initialDevices !== undefined) return;
    void load("/api/iot/devices").finally(() => setLoading(false));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const rescan = useCallback(async () => {
    setScanning(true);
    try {
      await load("/api/iot/scan", { method: "POST" });
    } finally {
      setScanning(false);
    }
  }, [load]);

  const groups = useMemo(() => groupByType(devices), [devices]);

  return (
    <div className="flex h-full min-h-0 flex-col">
      <header className="flex h-[52px] shrink-0 items-center gap-3 border-b border-border/40 bg-card/30 px-4">
        <div className="flex items-center gap-2">
          <span className="grid size-[26px] place-items-center rounded-lg bg-primary/10 text-primary">
            <Radar className="size-4" />
          </span>
          <span className="text-[14.5px] font-medium tracking-tight">Devices</span>
        </div>
        {devices.length > 0 && (
          <span className="inline-flex items-center rounded-full border border-border/40 bg-card px-2.5 py-0.5 text-[11.5px] text-muted-foreground">
            {devices.length} on your network
          </span>
        )}
        <div className="flex-1" />
        <button
          onClick={() => void rescan()}
          disabled={scanning}
          className="inline-flex h-[30px] items-center gap-1.5 rounded-lg border border-border/40 bg-card px-3 text-[12px] transition-colors hover:border-border disabled:opacity-60"
        >
          <RefreshCw className={cn("size-3.5", scanning && "animate-spin")} />
          {scanning ? "Scanning…" : "Rescan"}
        </button>
      </header>

      <div className="min-h-0 flex-1 overflow-y-auto">
        <div className="mx-auto w-full max-w-3xl px-4 py-6">
          {error && (
            <div className="mb-4 rounded-lg border border-amber-500/30 bg-amber-500/10 px-3.5 py-2.5 text-[12.5px] text-amber-700 dark:text-amber-400">
              {error}
            </div>
          )}

          {loading ? (
            <p className="py-16 text-center text-[13px] text-muted-foreground">
              Loading devices…
            </p>
          ) : devices.length === 0 ? (
            <div className="flex flex-col items-center gap-3 py-20 text-center">
              <span className="grid size-12 place-items-center rounded-full border border-dashed border-border text-muted-foreground">
                <Radar className="size-5" />
              </span>
              <p className="text-[13.5px] font-medium">No devices discovered yet</p>
              <p className="max-w-xs text-[12px] text-muted-foreground">
                Hit Rescan to sweep the local network for TVs, lights, speakers, and hubs.
              </p>
            </div>
          ) : (
            <>
              {groups.map(([type, list]) => (
                <section key={type} className="mb-6">
                  <h2 className="mb-1.5 flex items-baseline gap-2 px-3 text-[11px] font-medium tracking-[0.08em] text-muted-foreground/80 uppercase">
                    {typeMeta(type).label}
                    <span className="font-mono text-[10px] text-muted-foreground/50 normal-case">
                      {list.length}
                    </span>
                  </h2>
                  <ul className="rounded-xl border border-border/40 bg-card/20">
                    {list.map((d) => (
                      <DeviceRow key={deviceKey(d)} device={d} />
                    ))}
                  </ul>
                </section>
              ))}
              <p className="px-3 text-[11.5px] text-muted-foreground/70">
                Discovery-only preview — device control arrives in Phase 2.
              </p>
            </>
          )}
        </div>
      </div>
    </div>
  );
}
