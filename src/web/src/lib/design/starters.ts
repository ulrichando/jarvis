import type { Format } from "./format";

export type Starter = { title: string; prompt: string };

export const STARTERS: Record<Format, Starter[]> = {
  slides: [
    {
      title: "Coffee subscription pitch",
      prompt:
        "5-slide deck for a coffee subscription service called Kindling. Include one big-stat slide and one quote slide. Editorial type pairing.",
    },
    {
      title: "Seed round investor pitch",
      prompt:
        "Investor pitch for a $500K seed round, 8 slides — problem, insight, solution, demo screenshot, traction, market, team, ask. Modern sans aesthetic.",
    },
    {
      title: "Q3 internal kickoff",
      prompt:
        "Internal kickoff for Q3 priorities, 6 slides. Lead with three big numbers. Clear ownership per priority. Confident, founder-direct voice.",
    },
  ],
  prototype: [
    {
      title: "Reading-tracker iOS app",
      prompt:
        "iOS app for tracking daily reading time — three distinct screens: home with today's progress, library with the books I'm reading, and an active reading-timer screen.",
    },
    {
      title: "Lawyer caseload web app",
      prompt:
        "Web-app dashboard for a litigation lawyer's caseload — list view with status chips, case-detail screen with timeline + documents, weekly calendar of hearings.",
    },
    {
      title: "Marketplace checkout flow",
      prompt:
        "Mobile checkout flow for a marketplace — cart, address, payment, success. Real iconography, real product copy, 44pt tap targets.",
    },
  ],
  landing: [
    {
      title: "Legal-hearing scheduler",
      prompt:
        "Landing page for a B2B SaaS that schedules legal hearings — asymmetric hero with a real specific value statement, 3 sections each with a different layout, footer. No centered CTAs.",
    },
    {
      title: "N+1 query detector",
      prompt:
        "Landing page for a developer tool that catches N+1 queries before they ship — technical, terse, code-heavy. Use a monospace display font.",
    },
    {
      title: "Espresso machine launch",
      prompt:
        "Launch page for a new espresso machine. Editorial typography, image-led hero (use a real Unsplash photo of espresso if you know one, otherwise composed shapes). Single Reserve CTA, no pricing block.",
    },
  ],
  onepager: [
    {
      title: "Weekly team briefing",
      prompt:
        "Weekly team briefing for a 20-person startup — wins, blockers, focus for next week. A4 portrait, prints clean, horizontal bands of contrasting tone.",
    },
    {
      title: "ADR-OHADA explainer",
      prompt:
        "One-page explainer of OHADA arbitration for non-lawyers — what it is, when it applies, two real example scenarios, what to expect timeline-wise. A4 portrait.",
    },
    {
      title: "Board metrics one-pager",
      prompt:
        "Quarterly board metrics one-pager — three north-star numbers up top, then a short commentary block per metric explaining the why behind the number.",
    },
  ],
  infographic: [
    {
      title: "Cameroon ride-hailing market",
      prompt:
        "The 2026 Cameroon ride-hailing market in 6 stats, vertical poster. Mix chart types — at least one big-number, one comparison, one pictogram. Mark numbers (illustrative) at the source line.",
    },
    {
      title: "State of remote work in West Africa",
      prompt:
        "State of remote work in West Africa, vertical infographic. 5 data sections with distinct visualizations. Source line at bottom.",
    },
    {
      title: "AI's impact on design tools",
      prompt:
        "How AI changed design tools in 2026 — vertical infographic, 5 data sections, mix of bar/donut/sparkline/pictogram. Editorial typography.",
    },
  ],
};
