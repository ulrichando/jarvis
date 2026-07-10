// src/cli/src/bridge/chromePolicy.ts
//
// Enforces the web app's Settings → Jarvis in Chrome policy
// (~/.jarvis/settings.json → chrome.{defaultPolicy, blockedSites}) at the
// bridge — the single choke point every extension command passes through
// (ext_browse.ts::sendExtCommand serves both HTTP /api/ext_browse and the
// side-panel browser agent). Before this module the web settings were
// persisted but read by nothing: the extension enforces only its OWN
// chrome.storage blocklist (jarvis-screen settings.js / safety.js). That
// extension-side layer is unchanged and still applies independently; this
// makes the web-persisted policy actually mean something.
//
// Semantics (matching the settings UI, src/web/.../jarvis-in-chrome.tsx):
//   defaultPolicy "allow" → Jarvis works everywhere except blockedSites.
//   defaultPolicy "block" → Jarvis is blocked everywhere (the UI has no
//     per-site allow list; its copy says per-site allows live in the
//     extension — but a web-level "Block on all sites" is absolute here).
//   blockedSites entries are bare hosts (the UI normalizes scheme/path/www
//   away); a host matches an entry if equal or a subdomain of it — the same
//   rule as the extension's safety.js::hostInList.

import { readFileSync, statSync } from "node:fs";
import os from "node:os";
import path from "node:path";

export type ChromePolicy = {
  defaultPolicy: "allow" | "block";
  blockedSites: string[];
};

const DEFAULT_POLICY: ChromePolicy = { defaultPolicy: "allow", blockedSites: [] };

// Mirrors the extension's ALWAYS_ALLOWED_ACTIONS (background.js): site-agnostic
// meta commands for orienting / navigating away are never site-gated, so a
// blocked page can't trap the agent (it can still list tabs, go back, leave).
export const SITE_AGNOSTIC_ACTIONS = new Set([
  "get_url", "list_tabs", "activate_tab", "group_tabs", "back", "forward", "close_tab",
]);

// Same file the web app's settings store writes (src/web/src/lib/settings/store.ts).
let settingsFile = path.join(os.homedir(), ".jarvis", "settings.json");

let cached: { policy: ChromePolicy; mtimeMs: number } | null = null;

/** Read chrome.{defaultPolicy, blockedSites} from ~/.jarvis/settings.json.
 *  mtime-cached so the per-command cost is one stat(); a torn/invalid file
 *  (crash mid-write) keeps the last-known-good policy rather than failing
 *  open to defaults. Missing file → defaults (allow-all, matching the web
 *  schema's DEFAULT_SETTINGS). */
export function loadChromePolicy(): ChromePolicy {
  let mtimeMs: number;
  try {
    mtimeMs = statSync(settingsFile).mtimeMs;
  } catch {
    cached = null;
    return DEFAULT_POLICY; // no settings file yet — web defaults are allow-all
  }
  if (cached && cached.mtimeMs === mtimeMs) return cached.policy;
  try {
    const raw = JSON.parse(readFileSync(settingsFile, "utf-8"));
    const c = raw?.chrome ?? {};
    const policy: ChromePolicy = {
      defaultPolicy: c.defaultPolicy === "block" ? "block" : "allow",
      blockedSites: Array.isArray(c.blockedSites)
        ? c.blockedSites.filter((s: unknown): s is string => typeof s === "string").map((s: string) => s.toLowerCase())
        : [],
    };
    cached = { policy, mtimeMs };
    return policy;
  } catch {
    return cached?.policy ?? DEFAULT_POLICY;
  }
}

export function hostOf(url: string): string {
  try { return new URL(url).hostname.toLowerCase(); } catch { return ""; }
}

/** A host matches a list entry if it equals it or is a subdomain of it —
 *  identical semantics to the extension's safety.js::hostInList. */
export function hostInList(host: string, list: string[]): boolean {
  return list.some((h) => host === h || host.endsWith("." + h));
}

export type PolicyDecision = { allow: true } | { allow: false; reason: string };

/** Pure decision (unit-tested): given the action, the best-known target host
 *  (null/"" = unknown — e.g. content script unreachable on chrome://), and the
 *  persisted policy → allow or refuse. Unknown host under "allow" passes (a
 *  non-web page fails downstream anyway, same as the extension's gate);
 *  "block" refuses every site-gated action regardless of host. */
export function decideSitePolicy(
  action: string,
  host: string | null,
  policy: ChromePolicy,
): PolicyDecision {
  if (SITE_AGNOSTIC_ACTIONS.has(action)) return { allow: true };
  if (policy.defaultPolicy === "block") {
    return {
      allow: false,
      reason: 'Jarvis in Chrome is set to "Block on all sites" — change it in Jarvis Settings → Jarvis in Chrome to act on pages.',
    };
  }
  const h = (host ?? "").toLowerCase();
  if (h && hostInList(h, policy.blockedSites)) {
    return { allow: false, reason: `Jarvis is blocked on ${h} (Jarvis Settings → Jarvis in Chrome → Blocked sites).` };
  }
  return { allow: true };
}

// ── test hooks ────────────────────────────────────────────────────────
export function _setSettingsFileForTests(p: string | null) {
  settingsFile = p ?? path.join(os.homedir(), ".jarvis", "settings.json");
  cached = null;
}
