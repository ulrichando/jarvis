// In-process fixed-window rate limiter for the password-reset verify endpoint.
//
// SCOPE / TRADE-OFF: this is a single-instance app (one Next process; the proxy
// binds loopback). A plain in-memory Map keyed by email+IP is sufficient and
// matches how the rest of the app rate-limits. It does NOT survive a restart
// and would NOT coordinate across replicas — if this ever runs multi-instance,
// move this to a shared store (Redis / the Postgres `password_resets` table).
//
// Policy: at most MAX attempts per WINDOW_MS per (email + IP) key. Counting
// happens on each verify attempt regardless of success, so a brute-forcer
// can't get more than MAX guesses per window from one origin for one email.

const MAX = 5;
const WINDOW_MS = 15 * 60 * 1000; // 15 minutes

const attempts = new Map<string, number[]>();

/**
 * Record an attempt for `key` and report whether it is allowed (i.e. within the
 * MAX-per-WINDOW budget). Call exactly once per verify attempt. Returns false
 * once the limit is exceeded for the current window.
 */
export function rateLimitAllow(
  key: string,
  now: number = Date.now(),
): boolean {
  const cutoff = now - WINDOW_MS;
  const recent = (attempts.get(key) ?? []).filter((t) => t > cutoff);
  recent.push(now);
  attempts.set(key, recent);
  // Opportunistic cleanup so the Map doesn't grow unbounded over long uptimes.
  if (attempts.size > 10_000) {
    for (const [k, ts] of attempts) {
      const live = ts.filter((t) => t > cutoff);
      if (live.length === 0) attempts.delete(k);
      else attempts.set(k, live);
    }
  }
  return recent.length <= MAX;
}

/** Test-only: clear all recorded attempts. */
export function __resetRateLimitState(): void {
  attempts.clear();
}

export const RATE_LIMIT_MAX = MAX;
export const RATE_LIMIT_WINDOW_MS = WINDOW_MS;
