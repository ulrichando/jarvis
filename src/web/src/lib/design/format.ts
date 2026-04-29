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
