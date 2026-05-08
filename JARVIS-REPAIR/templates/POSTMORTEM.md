# Postmortem: `<incident title>`

- **Date of incident:** `<YYYY-MM-DD>`
- **Detected by:** `[ROLE]` / user / monitoring / test
- **Severity:** P0 | P1 | P2
- **Resolved:** yes | no | mitigated
- **Author:** `[ROLE]`
- **Related:** W-..., F-..., ADR-...

> **This postmortem is blameless.** We are studying the system, not assigning fault. Honest reporting is the goal; nothing here is used as evidence against any role.

---

## Summary

One paragraph. What happened, what was the impact, how was it resolved.

## Impact

- What broke
- Who / what was affected
- Duration of impact
- SLO violations (if any)

## Timeline

| Time | Event |
|---|---|
| `T+0` | Change merged: ... |
| `T+x` | First symptom observed: ... |
| `T+y` | Detected: ... |
| `T+z` | Mitigation applied: ... |
| `T+w` | Resolved: ... |

## Root cause

What was the actual cause? Be specific and technical. Avoid "human error" — that is never a root cause; it is a starting point. Ask "why was that failure mode possible at all?"

## Contributing factors

What else made this incident possible or worse than it should have been?

- ...
- ...

## What went well

What worked in detection, response, or mitigation? This matters — we want to keep these properties.

- ...

## What went poorly

Where did the system or process let us down?

- ...

## Where we got lucky

Latent bugs that did not bite us this time but could have. List them so we fix them before they do.

- ...

## Action items

Concrete, owned, deadlined. No "we should be more careful."

| Action | Owner | Type | Due |
|---|---|---|---|
| | `[ROLE]` | prevent / detect / mitigate / process | |

Each becomes a work item in `03-STATE.md`.

## Lessons learned

What does this teach us about JARVIS or about our process? Capture in one or two sentences. If it implies a Charter amendment, file the ADR.
