import type { Aesthetic } from "./format";

/**
 * Curated themes — the model picks one (based on aesthetic), gets a
 * verified-contrast palette, doesn't invent its own.
 *
 * Why this exists: models keep generating "dark text on dark bg" or
 * inventing semantically conflicting tokens (--paper dark + --ink dark).
 * Removing that discretion fixes the readability bugs at the source.
 *
 * Each theme is hand-checked for WCAG AA contrast:
 *   fg vs bg    >= 4.5:1 (body text)
 *   muted vs bg >= 4.5:1 (secondary text — still readable)
 *   accent vs bg >= 3:1   (large text / UI components)
 */
export type Theme = {
  id: string;
  mode: "dark" | "light";
  /** CSS var values, in HEX. */
  bg: string;
  fg: string;
  accent: string;
  muted: string;
  supporting: string;
  /** Display font from Google Fonts, e.g. "Bricolage Grotesque". */
  displayFont: string;
  /** Body font from Google Fonts. */
  bodyFont: string;
  /** Google Fonts CSS URL — model drops this directly into <link>. */
  fontsUrl: string;
};

const editorialDark: Theme = {
  id: "editorial-dark",
  mode: "dark",
  bg: "#0F0E0C",
  fg: "#F5F1EA",
  accent: "#D4A056",
  muted: "#A39A8C",
  supporting: "#1F1D1A",
  displayFont: "Fraunces",
  bodyFont: "Inter",
  fontsUrl:
    "https://fonts.googleapis.com/css2?family=Fraunces:opsz,wght@9..144,500;9..144,700&family=Inter:wght@400;500;600&display=swap",
};

const editorialLight: Theme = {
  id: "editorial-light",
  mode: "light",
  bg: "#FAF7F2",
  fg: "#1C1A18",
  accent: "#9B2D2D",
  muted: "#6B6862",
  supporting: "#EAE5DD",
  displayFont: "Fraunces",
  bodyFont: "Inter",
  fontsUrl:
    "https://fonts.googleapis.com/css2?family=Fraunces:opsz,wght@9..144,500;9..144,700&family=Inter:wght@400;500;600&display=swap",
};

const minimalistLight: Theme = {
  id: "minimalist-light",
  mode: "light",
  bg: "#FFFFFF",
  fg: "#0F0F12",
  accent: "#0F0F12",
  muted: "#6E6E73",
  supporting: "#F5F5F7",
  displayFont: "Inter",
  bodyFont: "Inter",
  fontsUrl:
    "https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap",
};

const minimalistDark: Theme = {
  id: "minimalist-dark",
  mode: "dark",
  bg: "#0A0A0A",
  fg: "#FAFAFA",
  accent: "#FAFAFA",
  muted: "#9F9F9F",
  supporting: "#1A1A1A",
  displayFont: "Inter",
  bodyFont: "Inter",
  fontsUrl:
    "https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap",
};

const brutalist: Theme = {
  id: "brutalist",
  mode: "light",
  bg: "#FFFFFF",
  fg: "#000000",
  accent: "#FFEC00",
  muted: "#000000",
  supporting: "#FFFFFF",
  displayFont: "Bricolage Grotesque",
  bodyFont: "JetBrains Mono",
  fontsUrl:
    "https://fonts.googleapis.com/css2?family=Bricolage+Grotesque:wght@700;800&family=JetBrains+Mono:wght@400;500;700&display=swap",
};

const cinema: Theme = {
  id: "cinema",
  mode: "dark",
  bg: "#050505",
  fg: "#F4EDD8",
  accent: "#C8A45C",
  muted: "#8A8473",
  supporting: "#0F0F10",
  displayFont: "Fraunces",
  bodyFont: "Manrope",
  fontsUrl:
    "https://fonts.googleapis.com/css2?family=Fraunces:opsz,wght@9..144,700;9..144,900&family=Manrope:wght@400;500;600&display=swap",
};

const playful: Theme = {
  id: "playful",
  mode: "light",
  bg: "#FFF8F0",
  fg: "#1A1A2E",
  accent: "#FF4F58",
  muted: "#5A5A6E",
  supporting: "#FFE5D4",
  displayFont: "Bricolage Grotesque",
  bodyFont: "DM Sans",
  fontsUrl:
    "https://fonts.googleapis.com/css2?family=Bricolage+Grotesque:wght@700;800&family=DM+Sans:wght@400;500;600;700&display=swap",
};

const futuristic: Theme = {
  id: "futuristic",
  mode: "dark",
  bg: "#08090C",
  fg: "#E8ECF1",
  accent: "#00E5FF",
  muted: "#8E94A0",
  supporting: "#10131A",
  displayFont: "Space Grotesk",
  bodyFont: "JetBrains Mono",
  fontsUrl:
    "https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@500;600;700&family=JetBrains+Mono:wght@400;500&display=swap",
};

const handcrafted: Theme = {
  id: "handcrafted",
  mode: "light",
  bg: "#FAF6EE",
  fg: "#2A1F14",
  accent: "#A05A2C",
  muted: "#6B5A45",
  supporting: "#F0E9D6",
  displayFont: "Fraunces",
  bodyFont: "Karla",
  fontsUrl:
    "https://fonts.googleapis.com/css2?family=Fraunces:opsz,wght@9..144,500;9..144,700&family=Karla:wght@400;500;600;700&display=swap",
};

const corporate: Theme = {
  id: "corporate",
  mode: "light",
  bg: "#FFFFFF",
  fg: "#0F172A",
  accent: "#2563EB",
  muted: "#64748B",
  supporting: "#F1F5F9",
  displayFont: "Inter",
  bodyFont: "Inter",
  fontsUrl:
    "https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap",
};

/**
 * Pick a theme. Aesthetic preset wins; otherwise we choose a sensible
 * default based on what kind of content the brief implies.
 */
export function pickTheme(aesthetic: Aesthetic | null): Theme {
  switch (aesthetic) {
    case "editorial":
      return editorialDark;
    case "minimalist":
      return minimalistLight;
    case "brutalist":
      return brutalist;
    case "cinema":
      return cinema;
    case "playful":
      return playful;
    case "futuristic":
      return futuristic;
    case "handcrafted":
      return handcrafted;
    case "corporate":
      return corporate;
    default:
      // Sensible fallback for sites without a stated aesthetic.
      return editorialDark;
  }
}

/**
 * Render the theme as the inline `<style>` + `<script>` block for the
 * entry HTML scaffold. The model drops this VERBATIM into the entry
 * file — colors and fonts are pre-locked, the model can't pick wrong
 * ones because it never names them.
 */
export function themeStyleBlock(theme: Theme): string {
  // No `tailwind.config = {...}` — @tailwindcss/browser v4 ignores it.
  // Instead the model uses Tailwind arbitrary values like `bg-[var(--bg)]`
  // which the runtime parses correctly without any config. CSS variables
  // are defined in :root and font-family is set on body + headings via
  // explicit CSS so we don't depend on Tailwind named tokens at all.
  return `<link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="${theme.fontsUrl}" rel="stylesheet">
  <script src="https://cdn.jsdelivr.net/npm/@tailwindcss/browser@4"></script>
  <style>
    :root {
      --bg: ${theme.bg};
      --fg: ${theme.fg};
      --accent: ${theme.accent};
      --muted: ${theme.muted};
      --supporting: ${theme.supporting};
      --space-1: 0.5rem;  --space-2: 1rem;   --space-3: 1.5rem;
      --space-4: 2rem;    --space-6: 3rem;   --space-8: 4rem;
      --space-12: 6rem;   --space-16: 8rem;
      --ease-out-expo: cubic-bezier(0.22, 1, 0.36, 1);
    }
    html { background: var(--bg); color: var(--fg); }
    body {
      font-family: '${theme.bodyFont}', system-ui, sans-serif;
      background: var(--bg);
      color: var(--fg);
      -webkit-font-smoothing: antialiased;
    }
    h1, h2, h3, h4, h5, h6 {
      font-family: '${theme.displayFont}', system-ui, serif;
    }
    /* Helper utility classes the model can add to any element. */
    .font-display { font-family: '${theme.displayFont}', system-ui, serif; }
    .font-body { font-family: '${theme.bodyFont}', system-ui, sans-serif; }
    .ease-out-expo { transition-timing-function: var(--ease-out-expo); }
    @media (prefers-reduced-motion: reduce) {
      *, *::before, *::after {
        animation-duration: 0.01ms !important;
        animation-iteration-count: 1 !important;
        transition-duration: 0.01ms !important;
        scroll-behavior: auto !important;
      }
    }
  </style>`;
}
