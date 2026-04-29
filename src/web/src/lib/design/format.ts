export const FORMATS = ["slides", "prototype", "landing", "onepager", "infographic"] as const;
export type Format = (typeof FORMATS)[number];

export const DEFAULT_FORMAT: Format = "slides";

export const FORMAT_LABEL: Record<Format, string> = {
  slides: "Slides",
  prototype: "Prototype",
  landing: "Landing",
  onepager: "One-pager",
  infographic: "Infographic",
};

export const FORMAT_FILE: Record<Format, string> = {
  slides: "slides.html",
  prototype: "prototype.html",
  landing: "landing.html",
  onepager: "onepager.html",
  infographic: "infographic.html",
};

/**
 * Pulls the format out of a workspace filename. The playbook names files by
 * format ("slides.html", "prototype.html", etc.) so the basename round-trips.
 * Used by the export menu to pick the right PDF page size without needing a
 * format chip selector in the UI.
 */
export function formatFromFilename(name: string): Format | null {
  const base = name.replace(/^.*\//, "").replace(/\.html?$/i, "").toLowerCase();
  // Strip variant suffixes like "slides-v2" → "slides"
  const stem = base.replace(/-v\d+$/, "");
  return (FORMATS as readonly string[]).includes(stem) ? (stem as Format) : null;
}

export type FontPairing = {
  id: string;
  display: { family: string; weights: string };
  body: { family: string; weights: string };
  bias: Format[];
};

export const FONT_PAIRINGS: FontPairing[] = [
  {
    id: "editorial",
    display: { family: "Playfair Display", weights: "wght@500;700;900" },
    body: { family: "Inter", weights: "wght@400;500;600" },
    bias: ["infographic", "onepager"],
  },
  {
    id: "modern-sans",
    display: { family: "Bricolage Grotesque", weights: "wght@600;700;800" },
    body: { family: "IBM Plex Sans", weights: "wght@400;500;600" },
    bias: ["landing", "prototype"],
  },
  {
    id: "technical",
    display: { family: "Space Grotesk", weights: "wght@500;700" },
    body: { family: "JetBrains Mono", weights: "wght@400;500;700" },
    bias: ["slides", "prototype"],
  },
  {
    id: "serif-warm",
    display: { family: "Fraunces", weights: "opsz,wght@9..144,500;9..144,700" },
    body: { family: "Inter", weights: "wght@400;500;600" },
    bias: ["onepager", "landing"],
  },
  {
    id: "editorial-modern",
    display: { family: "Newsreader", weights: "wght@500;700" },
    body: { family: "Manrope", weights: "wght@400;500;600;700" },
    bias: ["slides", "onepager"],
  },
];

export function pickFontPairing(format: Format, seed?: number): FontPairing {
  const biased = FONT_PAIRINGS.filter((p) => p.bias.includes(format));
  const pool = biased.length > 0 ? biased : FONT_PAIRINGS;
  const idx = seed != null ? Math.abs(seed) % pool.length : Math.floor(Math.random() * pool.length);
  return pool[idx];
}

export function googleFontsUrl(p: FontPairing): string {
  const display = `family=${encodeURIComponent(p.display.family).replace(/%20/g, "+")}:${p.display.weights}`;
  const body = `family=${encodeURIComponent(p.body.family).replace(/%20/g, "+")}:${p.body.weights}`;
  return `https://fonts.googleapis.com/css2?${display}&${body}&display=swap`;
}

/**
 * Heuristic format classifier — picks a format from natural-language text.
 * Used when the client doesn't pass an explicit `format` (the design tab
 * doesn't show format chips by default, matching Claude Design's "describe
 * and we'll figure out the shape" UX). Patterns are ordered by specificity:
 * more-specific terms first so "infographic" wins over "page", etc.
 *
 * Returns `slides` as the safe default when nothing matches — slides are the
 * most common ask and have the broadest layout vocabulary in the playbook.
 */
export function inferFormat(text: string | null | undefined): Format {
  if (!text) return DEFAULT_FORMAT;
  const t = text.toLowerCase();

  if (
    /\b(infographic|poster|data\s*viz|data\s*visuali[sz]ation|stats?\s+(card|sheet|poster)|chart\s+poster)\b/.test(
      t,
    )
  ) {
    return "infographic";
  }

  if (
    /\b(prototype|wireframe|app|ios|iphone|android|mobile|tablet|kiosk|screen\s+flow|ui\s+flow|screens?\s+for|tap\s+target|click[- ]through)\b/.test(
      t,
    )
  ) {
    return "prototype";
  }

  if (
    /\b(one[- ]?pager|brief(?:ing)?|board\s+report|memo|a4|brief\s+sheet|status\s+update)\b/.test(
      t,
    )
  ) {
    return "onepager";
  }

  if (
    /\b(landing|homepage|home\s+page|marketing\s+page|product\s+page|launch\s+page|hero\s+page)\b/.test(
      t,
    )
  ) {
    return "landing";
  }

  if (
    /\b(slide|deck|presentation|pitch\s*(deck)?|kickoff|pptx?|keynote|all-?hands)\b/.test(
      t,
    )
  ) {
    return "slides";
  }

  return DEFAULT_FORMAT;
}
