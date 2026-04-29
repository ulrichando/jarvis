# Design Tab v1 — Visual Rubric Results

Companion to `2026-04-29-design-tab-overhaul-design.md`. The plan's Task 15 calls for human visual scoring against a 5-axis rubric. Code-level checks (every playbook produces format-specific guidance, brand-aware prompts inject correctly, PDF export round-trips) all passed during the implementation review.

This file is the place to record visual scoring as you drive the rubric.

## How to drive

1. Open `/design` in the running dev server (port 3001).
2. **With brand cleared** (don't toggle Brand panel, or save an empty draft): click each format chip, run the prompt below, wait for `*.html` to appear in the file list, click it, score the preview.

| format | prompt to type |
|---|---|
| slides | `5-slide deck pitching a coffee subscription called Kindling` |
| prototype | `iOS app for tracking daily reading time, 3 screens` |
| landing | `landing page for a B2B SaaS that schedules legal hearings` |
| onepager | `weekly team briefing for a 20-person startup, this week's wins/blockers/next` |
| infographic | `the 2026 Cameroon ride-hailing market in 6 stats, vertical poster` |

3. Score each output 1–5 on five axes and fill the table below.
4. Set a brand (Brand toggle → fill name=Pretva, accent=#FF6A00, fonts Bricolage Grotesque + IBM Plex Sans → Save) and re-run the slides + onepager prompts. Confirm the accent color and brand fonts appear in the generated HTML.
5. Any axis < 4 → revise the matching block in `src/web/src/lib/design/playbooks.ts` and re-run that format until ≥ 4.

## Rubric (record results here)

### No-brand pass

| format | typography | layout | color | specificity | no-slop | avg | notes |
|---|---|---|---|---|---|---|---|
| slides       |  |  |  |  |  |  |  |
| prototype    |  |  |  |  |  |  |  |
| landing      |  |  |  |  |  |  |  |
| onepager     |  |  |  |  |  |  |  |
| infographic  |  |  |  |  |  |  |  |

### Brand-applied pass (Pretva)

| format | brand applied? | accent visible? | brand font visible? | notes |
|---|---|---|---|---|
| slides   |  |  |  |  |
| onepager |  |  |  |  |

## Axis definitions

- **typography (1–5):** does the display font feel deliberate? Hierarchy aggressive enough? Inter/Roboto/Poppins as display = ≤2.
- **layout (1–5):** asymmetric, content-led; centered "Welcome to X" hero or 8-tile feature grid = ≤2.
- **color (1–5):** intentional palette, not random AI gradient; every spacing on the 8pt grid.
- **specificity (1–5):** real names, real numbers, no "Lorem Solutions" / "Company X" / lorem ipsum.
- **no-slop (1–5):** zero of: lavender→teal hero, "Trusted by" with fictional logos, decorative emoji, generic stock laptop photos.

Target: average ≥ 4 across all five axes for at least one format end-to-end before declaring v1 ready.
