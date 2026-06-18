// Server-start hook. Runs the /code background loops in-process so they work
// even when no tab is open: auto-fix-CI and the routines scheduler. Self-hosted
// single instance; guarded against HMR re-imports stacking intervals. Both
// passes are also exposed as endpoints (autofix/tick, routines tick via cron)
// for a systemd timer if you prefer external scheduling.
export async function register(): Promise<void> {
  if (process.env.NEXT_RUNTIME !== "nodejs") return;
  if (process.env.JARVIS_CODE_LOOPS === "0") return; // kill switch
  const g = globalThis as { __jarvisCodeTimer?: ReturnType<typeof setInterval> };
  if (g.__jarvisCodeTimer) return;
  const origin = process.env.JARVIS_WEB_ORIGIN || "http://127.0.0.1:3000";
  console.log(`[code-loops] background ticks started (every 90s), origin=${origin}`);
  g.__jarvisCodeTimer = setInterval(() => {
    void (async () => {
      try {
        const { getStore } = await import("@/lib/bridge/db");
        const store = getStore();
        const { runAutofixTick, runAutomergeTick } = await import("@/lib/bridge/autofix");
        await runAutofixTick(store);
        await runAutomergeTick(store);
        const { runRoutinesTick } = await import("@/lib/bridge/routines-tick");
        const dispatched = await runRoutinesTick(store, origin);
        const { runReclaimTick } = await import("@/lib/bridge/reclaim");
        const reaped = await runReclaimTick(store);
        if (dispatched || reaped) {
          console.log(`[code-loops] tick: routines dispatched=${dispatched} containers reaped=${reaped}`);
        }
      } catch (err) {
        // best-effort; the next tick retries — but surface the failure so a
        // broken tick isn't silently dead forever.
        console.error("[code-loops] tick failed:", err instanceof Error ? err.stack || err.message : err);
      }
    })();
  }, 90_000);
}
