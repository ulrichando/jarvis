// Voice helpers for the Settings → General → Voice picker. Two engines:
//   - Kokoro (on-device, kokoro-fastapi :8880) — the REAL voices it serves,
//     fetched live via GET /api/tts/voices, not invented names.
//   - Edge (online, Microsoft's free neural TTS) — the static EDGE_VOICES
//     catalog below, mirroring the mobile app (jarvis-android EdgeTtsVoice.kt).
// Unset falls back to the server's env default (af_heart).

/** Kokoro voice id shape, e.g. af_heart / bm_george. Server-side gate for
 *  the /api/tts `voice` param (the value reaches an internal HTTP body). */
export const KOKORO_ID_RE = /^[a-z]{2}_[a-z0-9]+$/;

/** English Kokoro prefixes: af/am = US, bf/bm = UK. Voice mode is
 *  English-only — Kokoro's other prefixes (jf_/jm_ Japanese, zf_/zm_ Chinese,
 *  ef_/em_ Spanish, ff_ French, hf_/hm_ Hindi, if_/im_ Italian, pf_/pm_
 *  Portuguese) speak THAT language, so they're filtered out of the picker
 *  (/api/tts/voices) and guarded server-side in /api/tts against stale saved
 *  ids. Edge ids are en-* only already (exact-match EDGE_VOICES catalog). */
export function isEnglishKokoro(id: string): boolean {
  return /^(af|am|bf|bm)_/.test(id);
}

export type EdgeVoice = {
  id: string;
  label: string;
  gender: "M" | "F";
  region: "US" | "UK";
};

/** Microsoft Edge neural voices (online — no key, no local container).
 *  Mirrors the mobile app's catalog: male first, then female; en-GB/en-US
 *  only. Keep in sync with jarvis-android EdgeTtsVoice.kt. */
export const EDGE_VOICES: EdgeVoice[] = [
  { id: "en-GB-RyanNeural", label: "Ryan (British)", gender: "M", region: "UK" },
  { id: "en-GB-ThomasNeural", label: "Thomas (British)", gender: "M", region: "UK" },
  { id: "en-US-GuyNeural", label: "Guy (American)", gender: "M", region: "US" },
  { id: "en-US-ChristopherNeural", label: "Christopher (American)", gender: "M", region: "US" },
  { id: "en-US-EricNeural", label: "Eric (American)", gender: "M", region: "US" },
  { id: "en-US-AndrewNeural", label: "Andrew (American)", gender: "M", region: "US" },
  { id: "en-US-BrianNeural", label: "Brian (American)", gender: "M", region: "US" },
  { id: "en-US-RogerNeural", label: "Roger (American)", gender: "M", region: "US" },
  { id: "en-GB-SoniaNeural", label: "Sonia (British)", gender: "F", region: "UK" },
  { id: "en-GB-LibbyNeural", label: "Libby (British)", gender: "F", region: "UK" },
  { id: "en-US-AriaNeural", label: "Aria (American)", gender: "F", region: "US" },
  { id: "en-US-JennyNeural", label: "Jenny (American)", gender: "F", region: "US" },
  { id: "en-US-AvaNeural", label: "Ava (American)", gender: "F", region: "US" },
  { id: "en-US-EmmaNeural", label: "Emma (American)", gender: "F", region: "US" },
  { id: "en-US-MichelleNeural", label: "Michelle (American)", gender: "F", region: "US" },
];

const EDGE_VOICE_IDS = new Set(EDGE_VOICES.map((v) => v.id));

/** Exact-match allowlist — the id reaches an internal synth call, so never
 *  shape-trust it (unlike Kokoro ids, which Kokoro itself re-validates). */
export function isEdgeVoice(id: string): boolean {
  return EDGE_VOICE_IDS.has(id);
}

/** "en-US-GuyNeural" → "Guy (American)". Falls back to the raw id. */
export function edgeVoiceLabel(id: string): string {
  return EDGE_VOICES.find((v) => v.id === id)?.label ?? id;
}

const ACCENT: Record<string, string> = {
  af: "US · female",
  am: "US · male",
  bf: "UK · female",
  bm: "UK · male",
};

/** "af_heart" → "Heart", plus accent grouping via kokoroVoiceAccent. */
export function kokoroVoiceLabel(id: string): string {
  const name = id.split("_")[1] ?? id;
  return name.charAt(0).toUpperCase() + name.slice(1);
}

export function kokoroVoiceAccent(id: string): string {
  return ACCENT[id.slice(0, 2)] ?? id.slice(0, 2).toUpperCase();
}
