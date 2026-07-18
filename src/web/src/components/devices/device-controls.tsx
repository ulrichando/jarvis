"use client";

/**
 * Devices — per-device controls, driven strictly by the sidecar's
 * `capabilities` list. Render only what the device actually supports;
 * uncontrollable devices never get here (the row shows control_hint instead).
 *
 * Action routing: HA entities ("ha:*" ids) speak on/off/brightness/color/
 * set_temperature/volume/play/pause/open/close; webOS TVs speak power_on/
 * power_off/volume_up/volume_down/set_volume/mute/launch_app/set_input/
 * play/pause/stop. Every POST is optimistic locally + toasts the sidecar's
 * speakable error on failure.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { toast } from "sonner";
import {
  AppWindow,
  ChevronDown,
  Link2,
  Minus,
  MonitorPlay,
  Palette,
  Pause,
  Play,
  Plus,
  Square,
  Sun,
  Thermometer,
  Volume2,
  type LucideIcon,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { Switch } from "@/components/ui/switch";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import {
  caps,
  deviceKey,
  extractItems,
  isHomeAssistant,
  pairDevice,
  sendCommand,
  type IotDevice,
} from "./api";

/** A command runner: resolves to the sidecar's response data, or null on failure. */
export type RunCommand = (
  action: string,
  params?: Record<string, unknown>,
) => Promise<Record<string, unknown> | null>;

/**
 * Shared command path for every control on one device. Centralizes the
 * 428 (pairing) / 424 (HA unconfigured) handling so each control stays dumb.
 */
export function useDeviceCommand(
  device: IotDevice,
  onPairingNeeded?: () => void,
): RunCommand {
  const key = deviceKey(device);
  const name = device.name || device.ip;
  return useCallback<RunCommand>(
    async (action, params = {}) => {
      const out = await sendCommand(key, action, params);
      switch (out.kind) {
        case "ok":
          return out.data;
        case "needs_pairing":
          onPairingNeeded?.();
          toast.error(out.error, {
            description: "Use Pair on the device card, then accept the prompt on the TV.",
          });
          return null;
        case "ha_unconfigured":
          toast.error(out.error, {
            action: {
              label: "Connect",
              onClick: () =>
                document
                  .getElementById("ha-connect")
                  ?.scrollIntoView({ behavior: "smooth", block: "center" }),
            },
          });
          return null;
        default:
          toast.error(out.error || `${name}: command failed`);
          return null;
      }
    },
    [key, name, onPairingNeeded],
  );
}

export const hasPowerControl = (d: IotDevice): boolean => {
  const set = new Set(caps(d));
  return set.has("power") || set.has("on") || set.has("off");
};

/** On/off switch — webOS power_on/power_off, HA on/off. State is optimistic. */
export function PowerSwitch({
  device,
  run,
}: {
  device: IotDevice;
  run: RunCommand;
}) {
  const [on, setOn] = useState(false);
  const webos = !isHomeAssistant(device) && caps(device).includes("power");
  return (
    <Switch
      checked={on}
      aria-label={`Power — ${device.name || device.ip}`}
      onCheckedChange={(v) => {
        setOn(v); // optimistic — revert if the sidecar refuses
        void run(
          v ? (webos ? "power_on" : "on") : (webos ? "power_off" : "off"),
        ).then((ok) => {
          if (!ok) setOn(!v);
        });
      }}
    />
  );
}

/** Pair (LG TV accept-prompt handshake) — shown on webOS TVs and any 428 device. */
export function PairButton({
  device,
  onPaired,
}: {
  device: IotDevice;
  onPaired: () => void;
}) {
  const [pairing, setPairing] = useState(false);
  const pair = async () => {
    setPairing(true);
    toast.info("Accept the prompt on your TV");
    const res = await pairDevice(deviceKey(device));
    setPairing(false);
    if (res.ok) {
      toast.success(`${device.name || "TV"} paired`);
      onPaired();
    } else if (res.status === 428) {
      // Still waiting on the on-screen accept — the sidecar's words are speakable.
      toast.warning(res.error ?? "Accept the pairing prompt on the TV, then try again.");
    } else {
      toast.error(res.error ?? "Pairing failed");
    }
  };
  return (
    <button
      onClick={() => void pair()}
      disabled={pairing}
      className={cn(
        "inline-flex h-[28px] items-center gap-1.5 rounded-lg border border-border/40 bg-card px-2.5 text-[12px] text-foreground transition-colors hover:border-border disabled:opacity-60",
      )}
    >
      <Link2 className="size-3.5" />
      {pairing ? "Pairing…" : "Pair"}
    </button>
  );
}

// ── small shared bits ──────────────────────────────────────────────────────

const ICON_BTN =
  "inline-flex size-7 shrink-0 items-center justify-center rounded-lg border border-border/40 bg-card text-foreground transition-colors hover:border-border disabled:opacity-50";

function Row({
  label,
  icon: Icon,
  children,
}: {
  label: string;
  icon: LucideIcon;
  children: React.ReactNode;
}) {
  return (
    <div className="flex items-center gap-3 py-2">
      <span className="flex w-28 shrink-0 items-center gap-1.5 text-[12px] text-muted-foreground">
        <Icon className="size-3.5" />
        {label}
      </span>
      <div className="flex min-w-0 flex-1 items-center gap-2">{children}</div>
    </div>
  );
}

/** Debounce a control commit so sliders/steppers don't spam the sidecar. */
function useDebouncedCommit<T>(fn: (v: T) => void, ms: number) {
  const fnRef = useRef(fn);
  useEffect(() => {
    fnRef.current = fn;
  });
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  useEffect(
    () => () => {
      if (timer.current) clearTimeout(timer.current);
    },
    [],
  );
  return useCallback(
    (v: T) => {
      if (timer.current) clearTimeout(timer.current);
      timer.current = setTimeout(() => fnRef.current(v), ms);
    },
    [ms],
  );
}

function RangeSlider({
  value,
  onChange,
  ariaLabel,
}: {
  value: number;
  onChange: (v: number) => void;
  ariaLabel: string;
}) {
  return (
    <input
      type="range"
      min={0}
      max={100}
      step={1}
      value={value}
      aria-label={ariaLabel}
      onChange={(e) => onChange(Number(e.target.value))}
      className="h-1.5 w-full max-w-[220px] cursor-pointer accent-primary"
    />
  );
}

const hexToRgb = (hex: string): [number, number, number] => {
  const h = hex.replace("#", "");
  return [
    parseInt(h.slice(0, 2), 16) || 0,
    parseInt(h.slice(2, 4), 16) || 0,
    parseInt(h.slice(4, 6), 16) || 0,
  ];
};

/** Lazily-populated dropdown for webOS apps / inputs. */
function LazyPicker({
  label,
  icon: Icon,
  fetchAction,
  idKeys,
  onSelect,
  run,
}: {
  label: string;
  icon: LucideIcon;
  fetchAction: "get_apps" | "get_inputs";
  idKeys: string[];
  onSelect: (id: string) => void;
  run: RunCommand;
}) {
  const [items, setItems] = useState<
    { id: string; label: string }[] | null
  >(null);
  const [loading, setLoading] = useState(false);
  const [selected, setSelected] = useState<string | null>(null);

  const load = async () => {
    setLoading(true);
    const data = await run(fetchAction);
    setLoading(false);
    if (data) setItems(extractItems(data, idKeys));
  };

  return (
    <DropdownMenu
      onOpenChange={(open) => {
        if (open && items === null && !loading) void load();
      }}
    >
      <DropdownMenuTrigger
        render={
          <button
            className="inline-flex h-[28px] max-w-[220px] items-center gap-1.5 rounded-lg border border-border/40 bg-card px-2.5 text-[12px] text-foreground transition-colors hover:border-border"
            aria-label={label}
          />
        }
      >
        <Icon className="size-3.5 text-muted-foreground" />
        <span className="truncate">{selected ?? label}</span>
        <ChevronDown className="size-3 text-muted-foreground/70" />
      </DropdownMenuTrigger>
      <DropdownMenuContent align="start" sideOffset={6} className="max-h-64 w-60 overflow-y-auto p-1">
        {loading && (
          <div className="px-2 py-1.5 text-[12px] text-muted-foreground">Loading…</div>
        )}
        {!loading && items !== null && items.length === 0 && (
          <div className="px-2 py-1.5 text-[12px] text-muted-foreground">
            Nothing reported by the TV
          </div>
        )}
        {items?.map((it) => (
          <DropdownMenuItem
            key={it.id}
            onClick={() => {
              setSelected(it.label);
              onSelect(it.id);
            }}
            className="text-[12.5px]"
          >
            {it.label}
          </DropdownMenuItem>
        ))}
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

// ── the capability-driven control panel ────────────────────────────────────

export function DeviceControls({
  device,
  run,
}: {
  device: IotDevice;
  run: RunCommand;
}) {
  const set = new Set(caps(device));
  const ha = isHomeAssistant(device);

  const [brightness, setBrightness] = useState(75);
  const [color, setColor] = useState("#ffffff");
  const [volume, setVolume] = useState(20);
  const [muted, setMuted] = useState(false);
  const [temperature, setTemperature] = useState(21);

  const commitBrightness = useDebouncedCommit<number>(
    (v) => void run("brightness", { brightness_pct: v }),
    400,
  );
  const commitColor = useDebouncedCommit<string>(
    (hex) => void run("color", { rgb_color: hexToRgb(hex) }),
    400,
  );
  const commitVolume = useDebouncedCommit<number>(
    (v) => void run(ha ? "volume" : "set_volume", { volume: v }),
    400,
  );
  const commitTemperature = useDebouncedCommit<number>(
    (v) => void run("set_temperature", { temperature: v }),
    600,
  );

  const mediaButtons: { label: string; icon: LucideIcon; action: string }[] = [];
  if (set.has("media") || set.has("play"))
    mediaButtons.push({ label: "Play", icon: Play, action: "play" });
  if (set.has("media") || set.has("pause"))
    mediaButtons.push({ label: "Pause", icon: Pause, action: "pause" });
  if (!ha && set.has("media"))
    mediaButtons.push({ label: "Stop", icon: Square, action: "stop" });

  return (
    <div className="flex flex-col divide-y divide-border/30">
      {set.has("brightness") && (
        <Row label="Brightness" icon={Sun}>
          <RangeSlider
            value={brightness}
            ariaLabel={`Brightness — ${device.name || device.ip}`}
            onChange={(v) => {
              setBrightness(v);
              commitBrightness(v);
            }}
          />
          <span className="w-9 text-right font-mono text-[11px] text-muted-foreground">
            {brightness}%
          </span>
        </Row>
      )}

      {set.has("color") && (
        <Row label="Color" icon={Palette}>
          <input
            type="color"
            value={color}
            aria-label={`Color — ${device.name || device.ip}`}
            onChange={(e) => {
              setColor(e.target.value);
              commitColor(e.target.value);
            }}
            className="size-7 cursor-pointer rounded-lg border border-border/40 bg-card p-0.5"
          />
          <span className="font-mono text-[11px] text-muted-foreground">{color}</span>
        </Row>
      )}

      {set.has("volume") && (
        <Row label="Volume" icon={Volume2}>
          {!ha && (
            <button
              aria-label={`Volume down — ${device.name || device.ip}`}
              onClick={() => void run("volume_down")}
              className={ICON_BTN}
            >
              <Minus className="size-3.5" />
            </button>
          )}
          <RangeSlider
            value={volume}
            ariaLabel={`Volume — ${device.name || device.ip}`}
            onChange={(v) => {
              setVolume(v);
              commitVolume(v);
            }}
          />
          {!ha && (
            <button
              aria-label={`Volume up — ${device.name || device.ip}`}
              onClick={() => void run("volume_up")}
              className={ICON_BTN}
            >
              <Plus className="size-3.5" />
            </button>
          )}
          <span className="w-9 text-right font-mono text-[11px] text-muted-foreground">
            {volume}
          </span>
        </Row>
      )}

      {set.has("mute") && (
        <Row label="Mute" icon={Volume2}>
          <Switch
            checked={muted}
            aria-label={`Mute — ${device.name || device.ip}`}
            onCheckedChange={(v) => {
              setMuted(v); // optimistic
              void run("mute", { mute: v }).then((ok) => {
                if (!ok) setMuted(!v);
              });
            }}
          />
        </Row>
      )}

      {set.has("set_temperature") && (
        <Row label="Temperature" icon={Thermometer}>
          <button
            aria-label={`Cooler — ${device.name || device.ip}`}
            onClick={() => {
              const v = Math.max(10, temperature - 0.5);
              setTemperature(v);
              commitTemperature(v);
            }}
            className={ICON_BTN}
          >
            <Minus className="size-3.5" />
          </button>
          <span className="w-14 text-center font-mono text-[12px] text-foreground">
            {temperature.toFixed(1)}°
          </span>
          <button
            aria-label={`Warmer — ${device.name || device.ip}`}
            onClick={() => {
              const v = Math.min(32, temperature + 0.5);
              setTemperature(v);
              commitTemperature(v);
            }}
            className={ICON_BTN}
          >
            <Plus className="size-3.5" />
          </button>
        </Row>
      )}

      {mediaButtons.length > 0 && (
        <Row label="Media" icon={Play}>
          {mediaButtons.map((b) => (
            <button
              key={b.action}
              aria-label={`${b.label} — ${device.name || device.ip}`}
              onClick={() => void run(b.action)}
              className={ICON_BTN}
            >
              <b.icon className="size-3.5" />
            </button>
          ))}
        </Row>
      )}

      {(set.has("open") || set.has("close")) && (
        <Row label="Position" icon={MonitorPlay}>
          {set.has("open") && (
            <button
              aria-label={`Open — ${device.name || device.ip}`}
              onClick={() => void run("open")}
              className="inline-flex h-[28px] items-center rounded-lg border border-border/40 bg-card px-2.5 text-[12px] transition-colors hover:border-border"
            >
              Open
            </button>
          )}
          {set.has("close") && (
            <button
              aria-label={`Close — ${device.name || device.ip}`}
              onClick={() => void run("close")}
              className="inline-flex h-[28px] items-center rounded-lg border border-border/40 bg-card px-2.5 text-[12px] transition-colors hover:border-border"
            >
              Close
            </button>
          )}
        </Row>
      )}

      {set.has("apps") && (
        <Row label="Apps" icon={AppWindow}>
          <LazyPicker
            label="Launch an app"
            icon={AppWindow}
            fetchAction="get_apps"
            idKeys={["id", "appId", "app_id"]}
            onSelect={(id) => void run("launch_app", { app_id: id })}
            run={run}
          />
        </Row>
      )}

      {set.has("inputs") && (
        <Row label="Input" icon={MonitorPlay}>
          <LazyPicker
            label="Switch input"
            icon={MonitorPlay}
            fetchAction="get_inputs"
            idKeys={["id", "input_id"]}
            onSelect={(id) => void run("set_input", { input_id: id })}
            run={run}
          />
        </Row>
      )}
    </div>
  );
}
