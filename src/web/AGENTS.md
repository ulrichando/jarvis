<!-- BEGIN:nextjs-agent-rules -->
# This is NOT the Next.js you know

This version has breaking changes — APIs, conventions, and file structure may all differ from your training data. Read the relevant guide in `node_modules/next/dist/docs/` before writing any code. Heed deprecation notices.
<!-- END:nextjs-agent-rules -->

## K2.6 modes
The four `kimi-k2-{instant,thinking,agent,swarm}` model entries use the per-mode handler dispatcher in `src/lib/ai/kimi/`. To enable in dev, add to `.env.local`:

    KIMI_K2_MODES_ENABLED=1

Spec: `docs/superpowers/specs/2026-05-05-kimi-k2-modes-web-design.md`
Plan: `docs/superpowers/plans/2026-05-05-kimi-k2-modes-web.md`
