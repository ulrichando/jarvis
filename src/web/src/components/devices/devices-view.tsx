"use client";

/**
 * Devices — categorized control UI over the IoT sidecar (via /api/iot/*).
 *
 * Phase 2: devices are grouped by category (TVs, Lights, Plugs, …) and each
 * controllable device renders controls driven strictly by its `capabilities`
 * list — power/on-off toggles, brightness/volume sliders, color, temperature
 * steppers, media buttons, lazy app/input pickers. Truthful by design:
 * devices without capabilities still list, wearing their `control_hint` as a
 * muted reason instead of fake controls. 428 surfaces a Pair action (LG TV
 * accept-prompt), 424 points at the Home Assistant panel below the list.
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import {
  Blinds,
  ChevronRight,
  CircleHelp,
  Fan,
  Info,
  Lightbulb,
  Plug,
  Radar,
  RefreshCw,
  Router,
  Speaker,
  Thermometer,
  Tv,
  type LucideIcon,
} from "lucide-react";
import { cn } from "@/lib/utils";
import {
  caps,
  deviceKey,
  isControllable,
  isWebosTv,
  type IotDevice,
} from "./api";
import {
  DeviceControls,
  hasPowerControl,
  PairButton,
  PowerSwitch,
  useDeviceCommand,
} from "./device-controls";
import { HomeAssistantPanel } from "./ha-connect";

export { deviceKey };
export type { IotDevice };

// ── categories ─────────────────────────────────────────────────────────────

const CATEGORY_META: Record<string, { label: string; icon: LucideIcon }> = {
  tv: { label: "TVs", icon: Tv },
  light: { label: "Lights", icon: Lightbulb },
  plug: { label: "Plugs", icon: Plug },
  thermostat: { label: "Thermostats", icon: Thermometer },
  speaker: { label: "Speakers", icon: Speaker },
  fan: { label: "Fans", icon: Fan },
  cover: { label: "Covers", icon: Blinds },
  hub: { label: "Hubs & bridges", icon: Router },
  unknown: { label: "Unidentified", icon: CircleHelp },
};

const categoryMeta = (t: string) =>
  CATEGORY_META[t] ?? {
    label: t.charAt(0).toUpperCase() + t.slice(1),
    icon: CircleHelp,
  };

// Canonical section order; unmapped types land before "unknown".
const CATEGORY_ORDER = [
  "tv",
  "light",
  "plug",
  "thermostat",
  "speaker",
  "fan",
  "cover",
  "hub",
];

export function groupByType(devices: IotDevice[]): [string, IotDevice[]][] {
  const groups = new Map<string, IotDevice[]>();
  for (const d of devices) {
    const t = d.type || "unknown";
    const list = groups.get(t) ?? [];
    list.push(d);
    groups.set(t, list);
  }
  const rank = (t: string) => {
    const i = CATEGORY_ORDER.indexOf(t);
    if (i >= 0) return i;
    return t === "unknown" ? CATEGORY_ORDER.length + 1 : CATEGORY_ORDER.length;
  };
  return [...groups.entries()]
    .map(([t, list]): [string, IotDevice[]] => [
      t,
      // Controllable devices first within a section, then by name.
      [...list].sort(
        (a, b) =>
          Number(isControllable(b)) - Number(isControllable(a)) ||
          a.name.localeCompare(b.name),
      ),
    ])
    .sort((a, b) => rank(a[0]) - rank(b[0]) || a[0].localeCompare(b[0]));
}

// ── controllability pill ───────────────────────────────────────────────────

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

// ── one device row ─────────────────────────────────────────────────────────

function DeviceRow({
  device,
  onRefresh,
}: {
  device: IotDevice;
  onRefresh: () => void;
}) {
  const [open, setOpen] = useState(false);
  const [needsPairing, setNeedsPairing] = useState(false);
  const Icon = categoryMeta(device.type).icon;
  const controllable = isControllable(device);
  const pairable = isWebosTv(device) || needsPairing;

  const markNeedsPairing = useCallback(() => setNeedsPairing(true), []);
  const run = useDeviceCommand(device, markNeedsPairing);

  return (
    <li className="group">
      <div className="flex w-full items-center gap-3 rounded-lg px-3 py-2.5 transition-colors hover:bg-muted/50">
        <button
          onClick={() => setOpen((v) => !v)}
          aria-expanded={open}
          aria-label={`${device.name || device.hostname || device.ip} — details`}
          className="flex min-w-0 flex-1 items-center gap-3 text-left"
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
        {/* Quick power — sibling of the expand button (no nested interactives). */}
        {controllable && hasPowerControl(device) && (
          <PowerSwitch device={device} run={run} />
        )}
      </div>

      {open && (
        <div className="mx-3 mb-2 rounded-lg border border-border/40 bg-card/40 px-4 py-3">
          {controllable ? (
            <DeviceControls device={device} run={run} />
          ) : (
            <p className="flex items-start gap-1.5 text-[12px] text-muted-foreground">
              <Info className="mt-0.5 size-3.5 shrink-0" />
              {device.control_hint || "No local control path for this device yet."}
            </p>
          )}

          {pairable && (
            <div className="mt-3 flex flex-wrap items-center gap-2 border-t border-border/40 pt-3">
              <PairButton device={device} onPaired={onRefresh} />
              <span className="text-[11.5px] text-muted-foreground">
                {needsPairing
                  ? "This device asked for pairing — accept the prompt on its screen."
                  : "First command on this TV needs a one-time pairing accept."}
              </span>
            </div>
          )}

          <dl className="mt-3 grid grid-cols-1 gap-x-6 gap-y-1.5 border-t border-border/40 pt-3 text-[12px] sm:grid-cols-2">
            <Detail label="IP address" value={device.ip} mono />
            {device.mac && <Detail label="MAC" value={device.mac} mono />}
            {device.hostname && <Detail label="Hostname" value={device.hostname} mono />}
            {device.protocol.length > 0 && (
              <Detail label="Seen via" value={device.protocol.join(" · ")} />
            )}
            {caps(device).length > 0 && (
              <Detail label="Capabilities" value={caps(device).join(" · ")} />
            )}
            {device.last_seen ? (
              <Detail
                label="Last seen"
                value={new Date(device.last_seen * 1000).toLocaleString()}
              />
            ) : null}
          </dl>
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

// ── the page ───────────────────────────────────────────────────────────────

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
      if (body.devices) setDevices(body.devices);
    } catch {
      setError("Could not reach the web API.");
    }
  }, []);

  const refresh = useCallback(() => {
    void load("/api/iot/devices");
  }, [load]);

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
  const controllableCount = useMemo(
    () => devices.filter(isControllable).length,
    [devices],
  );

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
            {controllableCount > 0 && ` · ${controllableCount} controllable`}
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
            <div className="flex flex-col items-center gap-3 py-16 text-center">
              <span className="grid size-12 place-items-center rounded-full border border-dashed border-border text-muted-foreground">
                <Radar className="size-5" />
              </span>
              <p className="text-[13.5px] font-medium">No devices discovered yet</p>
              <p className="max-w-xs text-[12px] text-muted-foreground">
                Hit Rescan to sweep the local network, or connect Home Assistant
                below to bring in its lights, plugs, and thermostats.
              </p>
            </div>
          ) : (
            groups.map(([type, list]) => {
              const meta = categoryMeta(type);
              const SectionIcon = meta.icon;
              return (
                <section key={type} className="mb-6">
                  <h2 className="mb-1.5 flex items-center gap-2 px-3 text-[11px] font-medium tracking-[0.08em] text-muted-foreground/80 uppercase">
                    <SectionIcon className="size-3.5 text-muted-foreground/60" />
                    {meta.label}
                    <span className="font-mono text-[10px] text-muted-foreground/50 normal-case">
                      {list.length}
                    </span>
                  </h2>
                  <ul className="rounded-xl border border-border/40 bg-card/20">
                    {list.map((d) => (
                      <DeviceRow key={deviceKey(d)} device={d} onRefresh={refresh} />
                    ))}
                  </ul>
                </section>
              );
            })
          )}

          <HomeAssistantPanel onSaved={refresh} />
        </div>
      </div>
    </div>
  );
}
