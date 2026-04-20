// Local + remote current time. IANA timezone database via Intl.
// Prefer this over web scraping — deterministic, instant, offline.

import type { ToolRunner } from "../types.ts";

export const currentTimeTool: ToolRunner = {
  def: {
    name: "current_time",
    description:
      "Get the current date and time in a specific timezone (or UTC, or local). Use this for any 'what time is it in X' question — do not scrape the web for time.",
    input_schema: {
      type: "object",
      properties: {
        timezone: {
          type: "string",
          description:
            "IANA timezone name like 'Asia/Kolkata' (India), 'America/New_York', 'Europe/London', 'Africa/Douala'. Use 'UTC' for UTC or 'local' for the machine's timezone.",
        },
      },
      required: ["timezone"],
    },
  },
  async run(input: unknown) {
    const { timezone } = input as { timezone: string };
    if (typeof timezone !== "string" || timezone.length === 0) {
      return { output: "current_time: timezone is required", is_error: true };
    }
    const tz = timezone === "local" ? undefined : timezone;
    try {
      const now = new Date();
      const fmt = new Intl.DateTimeFormat("en-US", {
        timeZone: tz,
        weekday: "long",
        year: "numeric",
        month: "long",
        day: "numeric",
        hour: "numeric",
        minute: "2-digit",
        second: "2-digit",
        hour12: true,
        timeZoneName: "short",
      });
      const formatted = fmt.format(now);
      const iso = new Intl.DateTimeFormat("sv-SE", {
        timeZone: tz,
        year: "numeric", month: "2-digit", day: "2-digit",
        hour: "2-digit", minute: "2-digit", second: "2-digit",
      }).format(now).replace(" ", "T");
      return { output: `${formatted}\n(iso: ${iso}, tz: ${tz ?? "local"})` };
    } catch (err) {
      return {
        output: `current_time: invalid timezone "${timezone}". Use IANA names like 'Asia/Kolkata'. (${err instanceof Error ? err.message : String(err)})`,
        is_error: true,
      };
    }
  },
};
