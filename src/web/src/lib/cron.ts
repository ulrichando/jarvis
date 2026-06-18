/**
 * Tiny dependency-free 5-field cron matcher (min hour dom month dow). Used by
 * the routines background runner AND the calendar view, so it's client-safe
 * (pure, no server imports). Supports `*`, `N`, `A-B`, `* / K` steps, and
 * `A,B,C` lists. dow: 0 or 7 = Sunday (JS getDay()).
 */

function parseField(field: string, min: number, max: number): Set<number> {
  const out = new Set<number>();
  for (const part of field.split(",")) {
    let step = 1;
    let range = part;
    const slash = part.indexOf("/");
    if (slash >= 0) {
      step = parseInt(part.slice(slash + 1), 10) || 1;
      range = part.slice(0, slash);
    }
    let lo = min;
    let hi = max;
    if (range === "*" || range === "") {
      /* full range */
    } else if (range.includes("-")) {
      const [a, b] = range.split("-").map((n) => parseInt(n, 10));
      lo = a;
      hi = b;
    } else {
      lo = hi = parseInt(range, 10);
    }
    if (Number.isNaN(lo) || Number.isNaN(hi)) continue;
    for (let v = lo; v <= hi; v += step) if (v >= min && v <= max) out.add(v);
  }
  return out;
}

/** True if `date` (local time) satisfies the cron expression. */
export function cronMatches(cron: string, date: Date): boolean {
  const fields = cron.trim().split(/\s+/);
  if (fields.length !== 5) return false;
  const [minF, hrF, domF, monF, dowF] = fields;
  if (!parseField(minF, 0, 59).has(date.getMinutes())) return false;
  if (!parseField(hrF, 0, 23).has(date.getHours())) return false;
  if (!parseField(monF, 1, 12).has(date.getMonth() + 1)) return false;

  const doms = parseField(domF, 1, 31);
  const dows = parseField(dowF, 0, 7);
  if (dows.has(7)) dows.add(0); // 7 == Sunday
  const domMatch = doms.has(date.getDate());
  const dowMatch = dows.has(date.getDay());
  // Standard cron: when both day-of-month and day-of-week are restricted, the
  // entry matches if EITHER does; a `*` side doesn't constrain.
  const domStar = domF === "*";
  const dowStar = dowF === "*";
  if (domStar && dowStar) return true;
  if (domStar) return dowMatch;
  if (dowStar) return domMatch;
  return domMatch || dowMatch;
}

const DOW_NAMES = [
  "sunday",
  "monday",
  "tuesday",
  "wednesday",
  "thursday",
  "friday",
  "saturday",
];

/**
 * Parse a natural-language schedule into a cron (+ optional one-time `at`) for
 * the routines form. Handles the common forms: "hourly", "every day at 9am",
 * "weekdays at 8", "every monday at 10", "in 2 hours", "tomorrow at 9am",
 * "today at 3pm". Returns null when it can't parse (caller falls back).
 */
export function parseNaturalSchedule(
  input: string,
): { cron: string; at?: number; label: string } | null {
  const t = input.trim().toLowerCase();
  if (!t) return null;
  const hourOf = (): number | null => {
    const m = /at\s+(\d{1,2})(?::(\d{2}))?\s*(am|pm)?/.exec(t);
    if (!m) return null;
    let h = parseInt(m[1], 10);
    if (m[3] === "pm" && h < 12) h += 12;
    if (m[3] === "am" && h === 12) h = 0;
    return h >= 0 && h <= 23 ? h : null;
  };
  const ymd = (d: Date) => `${d.getMinutes()} ${d.getHours()} ${d.getDate()} ${d.getMonth() + 1} *`;

  const rel = /in\s+(\d+)\s*(min(?:ute)?s?|hours?|days?)/.exec(t);
  if (rel) {
    const n = parseInt(rel[1], 10);
    const u = rel[2];
    const ms = u.startsWith("min") ? n * 60000 : u.startsWith("hour") ? n * 3600000 : n * 86400000;
    const when = new Date(Date.now() + ms);
    return { cron: ymd(when), at: when.getTime(), label: `Once ${input.trim()}` };
  }
  const h = hourOf();
  const hh = h ?? 9;
  if (/\b(hourly|every hour)\b/.test(t)) return { cron: "0 * * * *", label: "Hourly" };
  if (/weekday/.test(t)) return { cron: `0 ${hh} * * 1-5`, label: `Weekdays at ${hh}:00` };
  for (let i = 0; i < 7; i++) {
    if (t.includes(DOW_NAMES[i]) && /\b(every|weekly|each)\b/.test(t)) {
      return { cron: `0 ${hh} * * ${i}`, label: `Weekly ${DOW_NAMES[i]} at ${hh}:00` };
    }
  }
  if (/tomorrow/.test(t)) {
    const when = new Date();
    when.setDate(when.getDate() + 1);
    when.setHours(hh, 0, 0, 0);
    return { cron: ymd(when), at: when.getTime(), label: `Once tomorrow at ${hh}:00` };
  }
  if (/\b(every day|daily)\b/.test(t)) return { cron: `0 ${hh} * * *`, label: `Daily at ${hh}:00` };
  if (h !== null) {
    // bare "at <time>" / "today at <time>" → next occurrence today/tomorrow
    const when = new Date();
    when.setHours(hh, 0, 0, 0);
    if (when.getTime() < Date.now()) when.setDate(when.getDate() + 1);
    return { cron: ymd(when), at: when.getTime(), label: `Once at ${hh}:00` };
  }
  return null;
}

/** True if the cron runs at all on `date`'s calendar day (ignores minute/hour).
 *  Used by the calendar view to place routines on days. */
export function cronRunsOnDay(cron: string, date: Date): boolean {
  const f = cron.trim().split(/\s+/);
  if (f.length !== 5) return false;
  const [, , domF, monF, dowF] = f;
  if (!parseField(monF, 1, 12).has(date.getMonth() + 1)) return false;
  const doms = parseField(domF, 1, 31);
  const dows = parseField(dowF, 0, 7);
  if (dows.has(7)) dows.add(0);
  const domStar = domF === "*";
  const dowStar = dowF === "*";
  if (domStar && dowStar) return true;
  if (domStar) return dows.has(date.getDay());
  if (dowStar) return doms.has(date.getDate());
  return doms.has(date.getDate()) || dows.has(date.getDay());
}

/**
 * Did a cron-matching minute occur in (lastRunAt, now]? Steps minute by minute
 * with a 2h look-back so a missed/slow tick (or brief downtime) still fires
 * once — without spamming one run per missed minute.
 */
export function cronIsDue(cron: string, lastRunAt: number | null, now: number): boolean {
  const LOOKBACK_MS = 2 * 60 * 60 * 1000;
  const start = Math.max(lastRunAt ?? 0, now - LOOKBACK_MS);
  for (let t = Math.floor(start / 60000) * 60000 + 60000; t <= now; t += 60000) {
    if (cronMatches(cron, new Date(t))) return true;
  }
  return false;
}
