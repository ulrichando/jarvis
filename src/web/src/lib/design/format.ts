export const FORMATS = ["slides", "prototype", "landing", "onepager", "infographic"] as const;
export type Format = (typeof FORMATS)[number];

// Most vague briefs are "I want a website / a page" rather than "I want
// a deck", so landing is the better default. Slides still wins when the
// brief mentions slide/deck/pitch keywords.
export const DEFAULT_FORMAT: Format = "landing";

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
 * Detects ONLY when the user has explicitly asked to be asked questions.
 * v0/Lovable/Bolt all just-design by default; questions back are opt-in.
 * Trigger phrases: "ask me", "ask questions", "questions first",
 * "clarify", "need more info", "few questions", "need details".
 */
export function userAskedForQuestions(text: string | null | undefined): boolean {
  const t = (text ?? "").trim();
  if (!t) return false;
  return /\b(ask\s+me\b|ask\s+(?:some\s+)?questions|questions?\s+first|clarify|need\s+more\s+info|need\s+(?:more\s+)?details|few\s+questions)\b/i.test(
    t,
  );
}

/**
 * Quick heuristic: is this brief too sparse to ship a design from? Kept
 * around for callers that want a "this brief is vague" signal — but no
 * longer triggers automatic questionnaire generation. Default behavior
 * is now design-with-assumptions; questions are opt-in via
 * `userAskedForQuestions()`.
 *
 * Rules (most → least obvious):
 *   1. Empty / whitespace → sparse.
 *   2. Explicit "ask me first" / "questions" / "clarify" intent → sparse.
 *   3. Long brief (>=14 words) → not sparse, regardless of content.
 *   4. Very short (<=4 words) with no proper noun → sparse.
 *   5. Verb-only opener with no concrete subject — "design something cool",
 *      "make me a deck", "build an app" — sparse.
 *   6. No proper noun AND no quoted name AND no "for <something>"
 *      qualifier AND <=10 words → sparse. This catches generic phrasings
 *      like "design a restaurant landing page" or "make a pitch deck for
 *      a coffee shop" where there's no actual product/audience pinned down.
 */
export function isSparseBrief(text: string | null | undefined): boolean {
  const raw = (text ?? "").trim();
  if (!raw) return true;
  const t = raw;
  const lower = t.toLowerCase();
  const words = t.split(/\s+/);

  // Explicit user intent to be asked questions wins over everything.
  if (
    /\b(ask\s+me\b|ask\s+questions|need\s+more\s+info|clarify|questions?\s+first|few\s+questions)\b/i.test(
      t,
    )
  ) {
    return true;
  }

  // Long briefs are specific by definition.
  if (words.length >= 14) return false;

  // Very short → sparse unless it contains a proper noun (e.g. "Kindling deck").
  const hasProperNoun = /\b[A-Z][a-z]{2,}/.test(t);
  if (words.length <= 4 && !hasProperNoun) return true;

  // Verb-only opener with generic completion.
  if (
    /^(make|build|design|create|generate|do|whip\s+up)\s+(me\s+|us\s+)?(a|an|some|the)?\s*(thing|something|cool|nice|fun|simple|quick|deck|slide|slides|presentation|pitch|page|website|webpage|landing|app|prototype|design|poster|infographic|onepager|one-pager|mock|mockup|wireframe)?\s*[.!?]?\s*$/i.test(
      lower,
    )
  ) {
    return true;
  }

  // Medium-length but no concrete anchor. Without a proper noun (named
  // brand, person, place) or a quoted name ("called Kindling"), a 5-10
  // word brief is essentially "design <category> for <vague-thing>" —
  // the model has nothing specific to anchor against. Ask first.
  //
  // Examples that should be sparse:
  //   "i want a website for a restaurant"          (7 words, no name)
  //   "design a landing page for my startup"       (7 words, no name)
  //   "make a pitch deck for a coffee company"     (8 words, no name)
  // Examples that should NOT be sparse:
  //   "design a landing page for Pretva"           (proper noun)
  //   "5-slide pitch for a startup called Kindling" (quoted name)
  //   "build a landing page for an AI tutoring service for high school students" (>=14 words)
  const hasQuotedName = /"[^"]{2,}"|'[^']{2,}'|called\s+\w/i.test(t);
  if (words.length <= 10 && !hasProperNoun && !hasQuotedName) {
    return true;
  }

  return false;
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
    /\b(one[- ]?pager|board\s+report|memo|a4(?:\s+(sheet|document|page))?|brief\s+(sheet|document|note)|briefing\s+(sheet|document)|status\s+update|exec\s+summary)\b/.test(
      t,
    )
  ) {
    return "onepager";
  }

  if (
    /\b(landing|homepage|home\s+page|marketing\s+page|product\s+page|launch\s+page|hero\s+page|web\s*site|web\s*page|site|web\s+presence|online\s+presence)\b/.test(
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

// Aesthetic presets. Detecting one from the brief gives the model a
// concrete style anchor instead of letting it default to "AI-generic
// dark dashboard". Each preset expands into a detailed style brief
// (typography, palette, layout tendencies, anti-patterns) injected as
// a high-priority block in the system prompt.
export const AESTHETICS = [
  "editorial",
  "brutalist",
  "minimalist",
  "cinema",
  "playful",
  "futuristic",
  "handcrafted",
  "corporate",
] as const;
export type Aesthetic = (typeof AESTHETICS)[number];

/** Detect an explicit aesthetic keyword in the brief. */
export function inferAesthetic(
  text: string | null | undefined,
): Aesthetic | null {
  if (!text) return null;
  const t = text.toLowerCase();
  if (
    /\b(editorial|magazine|vogue|new\s*yorker|nyt|times|the\s+atlantic|long\s*form)\b/.test(
      t,
    )
  ) {
    return "editorial";
  }
  if (
    /\b(brutalist|brutal|raw|swiss|naked|unstyled|wireframe[- ]?ish|terminal)\b/.test(
      t,
    )
  ) {
    return "brutalist";
  }
  if (
    /\b(minimalist|minimal|spare|whitespace|restrained|swiss\s*minimal|muji|dieter\s*rams|braun)\b/.test(
      t,
    )
  ) {
    return "minimalist";
  }
  if (
    /\b(cinema|cinematic|wes\s*anderson|tarantino|movie|film|trailer|theatrical|widescreen)\b/.test(
      t,
    )
  ) {
    return "cinema";
  }
  if (
    /\b(playful|fun|whimsical|bright|colorful|cute|kawaii|nintendo|figma\s*party)\b/.test(
      t,
    )
  ) {
    return "playful";
  }
  if (
    /\b(futuristic|cyberpunk|neon|sci[- ]?fi|y2k|vaporwave|holographic|techwear|matrix)\b/.test(
      t,
    )
  ) {
    return "futuristic";
  }
  if (
    /\b(handcrafted|handmade|artisan|organic|warm|hand[- ]?drawn|sketchy|illustrative|paper)\b/.test(
      t,
    )
  ) {
    return "handcrafted";
  }
  if (
    /\b(corporate|enterprise|b2b\s*saas|professional|business[- ]?like|fortune\s*500)\b/.test(
      t,
    )
  ) {
    return "corporate";
  }
  return null;
}
