// Per-design tweakable parameters. A design declares its own tweaks via a
// JSON block (`<script type="application/json" id="jarvis-tweaks">[...]</script>`)
// embedded in the HTML. The right-side Tweaks panel renders one control per
// declared tweak, and live updates flow back to the iframe via postMessage.

export type ColorSwatchesTweak = {
  id: string;
  label: string;
  type: "color-swatches";
  value: string;
  options: string[];
};

export type RangeTweak = {
  id: string;
  label: string;
  type: "range";
  value: number;
  min: number;
  max: number;
  step: number;
  suffix?: string;
};

export type SegmentedTweak = {
  id: string;
  label: string;
  type: "segmented";
  value: string;
  options: { value: string; label: string }[];
};

export type ToggleTweak = {
  id: string;
  label: string;
  type: "toggle";
  value: boolean;
};

export type TextTweak = {
  id: string;
  label: string;
  type: "text";
  value: string;
  placeholder?: string;
  maxLength?: number;
};

export type Tweak =
  | ColorSwatchesTweak
  | RangeTweak
  | SegmentedTweak
  | ToggleTweak
  | TextTweak;

const ID_RX = /^[a-z][a-z0-9_]*$/;

function isHex(s: unknown): s is string {
  return typeof s === "string" && /^#[0-9a-fA-F]{3,8}$/.test(s);
}

function isValidTweak(x: unknown): x is Tweak {
  if (!x || typeof x !== "object") return false;
  const t = x as Record<string, unknown>;
  if (typeof t.id !== "string" || !ID_RX.test(t.id)) return false;
  if (typeof t.label !== "string" || t.label.length === 0) return false;
  switch (t.type) {
    case "color-swatches":
      return (
        isHex(t.value) &&
        Array.isArray(t.options) &&
        t.options.length > 0 &&
        t.options.every(isHex)
      );
    case "range":
      return (
        typeof t.value === "number" &&
        typeof t.min === "number" &&
        typeof t.max === "number" &&
        typeof t.step === "number" &&
        t.min < t.max
      );
    case "segmented":
      return (
        typeof t.value === "string" &&
        Array.isArray(t.options) &&
        t.options.every(
          (o) =>
            o &&
            typeof o === "object" &&
            typeof (o as { value: unknown }).value === "string" &&
            typeof (o as { label: unknown }).label === "string",
        )
      );
    case "toggle":
      return typeof t.value === "boolean";
    case "text":
      return typeof t.value === "string";
    default:
      return false;
  }
}

const TWEAKS_RX = /<script[^>]+id=["']jarvis-tweaks["'][^>]*>([\s\S]*?)<\/script>/i;

/** Parse the embedded `<script id="jarvis-tweaks">…</script>` JSON block. */
export function extractTweaks(html: string): Tweak[] {
  const m = html.match(TWEAKS_RX);
  if (!m) return [];
  try {
    const parsed = JSON.parse(m[1].trim());
    if (!Array.isArray(parsed)) return [];
    return parsed.filter(isValidTweak);
  } catch {
    return [];
  }
}

/** Message sent from the parent into the iframe when a tweak changes. The
 *  iframe-side script (see PICKER_SCRIPT in design-preview.tsx) listens and
 *  applies the change as a CSS variable, data-attribute, or text replacement. */
export type TweakMessage =
  | { type: "jarvis:design:tweak"; id: string; kind: "color-swatches" | "range"; value: string | number }
  | { type: "jarvis:design:tweak"; id: string; kind: "segmented" | "toggle"; value: string | boolean }
  | { type: "jarvis:design:tweak"; id: string; kind: "text"; value: string };

export function tweakToMessage(t: Tweak, value: Tweak["value"]): TweakMessage {
  return {
    type: "jarvis:design:tweak",
    id: t.id,
    kind: t.type,
    value: value as never,
  } as TweakMessage;
}
