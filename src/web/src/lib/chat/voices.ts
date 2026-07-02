// Kokoro voice helpers. The picker (Settings → General → Voice) lists the
// REAL voices the local kokoro-fastapi (:8880) serves — fetched live via
// GET /api/tts/voices — not invented names. Unset falls back to the
// server's env default (af_heart).

/** Kokoro voice id shape, e.g. af_heart / bm_george. Server-side gate for
 *  the /api/tts `voice` param (the value reaches an internal HTTP body). */
export const KOKORO_ID_RE = /^[a-z]{2}_[a-z0-9]+$/;

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
