/**
 * Devices — shared types + fetch helpers for the IoT sidecar (via /api/iot/*).
 *
 * All calls go through the authed catch-all forwarder at
 * src/app/api/iot/[...path]/route.ts → 127.0.0.1:8779. Status codes are
 * meaningful: 428 = device needs pairing (LG TV accept-prompt), 424 = Home
 * Assistant not configured yet. Errors from the sidecar are speakable —
 * surface them verbatim.
 */

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
  /** Stable controller id when one exists (e.g. "ha:light.kitchen"). */
  id?: string | null;
  /** Actions the sidecar can actually drive. Empty/absent = discovery-only. */
  capabilities?: string[];
  first_seen?: number;
  last_seen?: number;
};

/** Registry key for command URLs, mirroring the sidecar: id → mac → ip:<ip>. */
export const deviceKey = (d: IotDevice): string => d.id || d.mac || `ip:${d.ip}`;

export const caps = (d: IotDevice): string[] => d.capabilities ?? [];

/** Truthful controllability: only devices the sidecar can actually drive. */
export const isControllable = (d: IotDevice): boolean => caps(d).length > 0;

/** Home Assistant entities carry an "ha:<domain>.<name>" id. */
export const isHomeAssistant = (d: IotDevice): boolean =>
  (d.id ?? "").startsWith("ha:");

/** LG webOS TVs pair over the local network (428 → accept prompt on the TV). */
export const isWebosTv = (d: IotDevice): boolean =>
  d.protocol.some((p) => p.toLowerCase().includes("webos")) ||
  (!isHomeAssistant(d) && d.type === "tv" && caps(d).includes("power"));

export type CommandOutcome =
  | { kind: "ok"; data: Record<string, unknown> }
  | { kind: "needs_pairing"; error: string }
  | { kind: "ha_unconfigured"; error: string }
  | { kind: "error"; error: string };

export async function sendCommand(
  key: string,
  action: string,
  params: Record<string, unknown> = {},
): Promise<CommandOutcome> {
  try {
    const res = await fetch(
      `/api/iot/devices/${encodeURIComponent(key)}/command`,
      {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ action, ...params }),
      },
    );
    const body = (await res.json().catch(() => ({}))) as Record<string, unknown>;
    const error =
      typeof body.error === "string" && body.error
        ? body.error
        : `HTTP ${res.status}`;
    if (res.status === 428) return { kind: "needs_pairing", error };
    if (res.status === 424) return { kind: "ha_unconfigured", error };
    if (!res.ok || body.ok === false) return { kind: "error", error };
    return { kind: "ok", data: body };
  } catch {
    return { kind: "error", error: "IoT service unreachable" };
  }
}

/** POST /devices/{key}/connect — LG TV pairing handshake. */
export async function pairDevice(
  key: string,
): Promise<{ ok: boolean; status: number; error?: string }> {
  try {
    const res = await fetch(
      `/api/iot/devices/${encodeURIComponent(key)}/connect`,
      { method: "POST", headers: { "content-type": "application/json" } },
    );
    const body = (await res.json().catch(() => ({}))) as Record<string, unknown>;
    return {
      ok: res.ok && body.ok !== false,
      status: res.status,
      error: typeof body.error === "string" ? body.error : undefined,
    };
  } catch {
    return { ok: false, status: 0, error: "IoT service unreachable" };
  }
}

// ── Home Assistant config ──────────────────────────────────────────────────

export type HaConfig = { url: string; token_set: boolean };

export async function fetchHaConfig(): Promise<HaConfig | null> {
  try {
    const res = await fetch("/api/iot/config");
    if (!res.ok) return null;
    const body = (await res.json()) as {
      home_assistant?: { url?: string; token_set?: boolean };
    };
    return {
      url: body.home_assistant?.url ?? "",
      token_set: body.home_assistant?.token_set ?? false,
    };
  } catch {
    return null;
  }
}

/** URL-only saves preserve the stored token (sidecar contract). */
export async function saveHaConfig(
  url: string,
  token: string,
): Promise<{ ok: boolean; error?: string }> {
  try {
    const res = await fetch("/api/iot/config", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({
        home_assistant: token ? { url, token } : { url },
      }),
    });
    const body = (await res.json().catch(() => ({}))) as Record<string, unknown>;
    if (!res.ok || body.ok === false) {
      return {
        ok: false,
        error:
          typeof body.error === "string" ? body.error : `HTTP ${res.status}`,
      };
    }
    return { ok: true };
  } catch {
    return { ok: false, error: "IoT service unreachable" };
  }
}

// ── Lazy pickers (webOS apps / inputs) ─────────────────────────────────────

export type PickerItem = { id: string; label: string };

/**
 * get_apps / get_inputs response shapes vary by TV firmware — find the first
 * array of objects anywhere shallow in the payload and read id/label-ish keys.
 */
export function extractItems(data: unknown, idKeys: string[]): PickerItem[] {
  const arr = findObjectArray(data, 0);
  if (!arr) return [];
  const items: PickerItem[] = [];
  for (const raw of arr) {
    if (!raw || typeof raw !== "object") continue;
    const rec = raw as Record<string, unknown>;
    const id = firstString(rec, idKeys);
    if (!id) continue;
    items.push({ id, label: firstString(rec, ["title", "label", "name"]) ?? id });
  }
  return items;
}

function firstString(rec: Record<string, unknown>, keys: string[]): string | undefined {
  for (const k of keys) {
    const v = rec[k];
    if (typeof v === "string" && v) return v;
  }
  return undefined;
}

function findObjectArray(v: unknown, depth: number): unknown[] | null {
  if (Array.isArray(v)) {
    return v.length > 0 && typeof v[0] === "object" ? v : null;
  }
  if (!v || typeof v !== "object" || depth > 2) return null;
  for (const val of Object.values(v)) {
    const hit = findObjectArray(val, depth + 1);
    if (hit) return hit;
  }
  return null;
}
