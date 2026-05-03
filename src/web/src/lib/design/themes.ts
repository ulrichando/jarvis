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
  id: "cinema-gold",
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

const cinemaTeal: Theme = {
  id: "cinema-teal",
  mode: "dark",
  bg: "#06090B",
  fg: "#E6EEEA",
  accent: "#3DA89E",
  muted: "#7A8A86",
  supporting: "#0E1517",
  displayFont: "Anton",
  bodyFont: "Manrope",
  fontsUrl:
    "https://fonts.googleapis.com/css2?family=Anton&family=Manrope:wght@400;500;600&display=swap",
};

const cinemaBlood: Theme = {
  id: "cinema-blood",
  mode: "dark",
  bg: "#0A0507",
  fg: "#F2E6E1",
  accent: "#B0413E",
  muted: "#8A7872",
  supporting: "#160B0E",
  displayFont: "Playfair Display",
  bodyFont: "Manrope",
  fontsUrl:
    "https://fonts.googleapis.com/css2?family=Playfair+Display:wght@700;900&family=Manrope:wght@400;500;600&display=swap",
};

const playful: Theme = {
  id: "playful-coral",
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

const playfulMint: Theme = {
  id: "playful-mint",
  mode: "light",
  bg: "#F0FBF6",
  fg: "#0F2E22",
  accent: "#10B981",
  muted: "#5C6E66",
  supporting: "#D1F2E2",
  displayFont: "Bricolage Grotesque",
  bodyFont: "DM Sans",
  fontsUrl:
    "https://fonts.googleapis.com/css2?family=Bricolage+Grotesque:wght@700;800&family=DM+Sans:wght@400;500;600;700&display=swap",
};

const corporateSlate: Theme = {
  id: "corporate-slate",
  mode: "light",
  bg: "#F8FAFC",
  fg: "#0F172A",
  accent: "#0F766E",
  muted: "#475569",
  supporting: "#E2E8F0",
  displayFont: "Inter",
  bodyFont: "Inter",
  fontsUrl:
    "https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap",
};

const handcraftedRust: Theme = {
  id: "handcrafted-rust",
  mode: "light",
  bg: "#F5EFE2",
  fg: "#2A140A",
  accent: "#A04020",
  muted: "#705542",
  supporting: "#E8DCC8",
  displayFont: "Newsreader",
  bodyFont: "Karla",
  fontsUrl:
    "https://fonts.googleapis.com/css2?family=Newsreader:opsz,wght@6..72,500;6..72,700&family=Karla:wght@400;500;600;700&display=swap",
};

const brutalistMagenta: Theme = {
  id: "brutalist-magenta",
  mode: "light",
  bg: "#FFFFFF",
  fg: "#000000",
  accent: "#FF0080",
  muted: "#000000",
  supporting: "#FFFFFF",
  displayFont: "Bricolage Grotesque",
  bodyFont: "JetBrains Mono",
  fontsUrl:
    "https://fonts.googleapis.com/css2?family=Bricolage+Grotesque:wght@700;800&family=JetBrains+Mono:wght@400;500;700&display=swap",
};

const brutalistLime: Theme = {
  id: "brutalist-lime",
  mode: "light",
  bg: "#FFFFFF",
  fg: "#000000",
  accent: "#84CC16",
  muted: "#000000",
  supporting: "#FFFFFF",
  displayFont: "Bricolage Grotesque",
  bodyFont: "JetBrains Mono",
  fontsUrl:
    "https://fonts.googleapis.com/css2?family=Bricolage+Grotesque:wght@700;800&family=JetBrains+Mono:wght@400;500;700&display=swap",
};

const futuristic: Theme = {
  id: "futuristic-cyan",
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

const futuristicEmerald: Theme = {
  id: "futuristic-emerald",
  mode: "dark",
  bg: "#06100C",
  fg: "#E5F5EE",
  accent: "#34D399",
  muted: "#7A9A8E",
  supporting: "#0C1A14",
  displayFont: "Bricolage Grotesque",
  bodyFont: "JetBrains Mono",
  fontsUrl:
    "https://fonts.googleapis.com/css2?family=Bricolage+Grotesque:wght@600;700;800&family=JetBrains+Mono:wght@400;500&display=swap",
};

const futuristicViolet: Theme = {
  id: "futuristic-violet",
  mode: "dark",
  bg: "#0B0817",
  fg: "#ECE7F5",
  accent: "#A78BFA",
  muted: "#8C84A8",
  supporting: "#15102A",
  displayFont: "Space Grotesk",
  bodyFont: "Inter",
  fontsUrl:
    "https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@500;600;700&family=Inter:wght@400;500;600&display=swap",
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

// Multiple colorways per aesthetic so different workspaces with the
// same aesthetic don't all look identical, and "redesign" follow-ups
// can rotate to a fresh palette while staying inside the aesthetic.
// First entry is the canonical / safest pick; later entries provide
// variety the seed will rotate through.
const THEMES_BY_AESTHETIC: Record<Aesthetic, Theme[]> = {
  editorial: [editorialDark, editorialLight],
  minimalist: [minimalistLight, minimalistDark],
  brutalist: [brutalist, brutalistMagenta, brutalistLime],
  cinema: [cinema, cinemaTeal, cinemaBlood],
  playful: [playful, playfulMint],
  futuristic: [futuristic, futuristicEmerald, futuristicViolet],
  handcrafted: [handcrafted, handcraftedRust],
  corporate: [corporate, corporateSlate],
};

/**
 * Pick a theme inside the chosen aesthetic. The seed rotates the
 * choice — so two workspaces with the same aesthetic don't end up
 * with identical hex values, AND a "redesign" turn that bumps the
 * seed gets a fresh palette without changing aesthetic.
 *
 * Seed is typically `workspaceHash * 7 + redesignCounter` from the
 * chat route. A stable seed (no redesign) keeps the theme consistent
 * across edits within a workspace.
 */
export function pickTheme(
  aesthetic: Aesthetic | null,
  seed: number = 0,
): Theme {
  const variants = aesthetic ? THEMES_BY_AESTHETIC[aesthetic] : null;
  if (!variants || variants.length === 0) return editorialDark;
  return variants[Math.abs(Math.trunc(seed)) % variants.length];
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
