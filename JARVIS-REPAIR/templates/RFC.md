# RFC-`<n>`: `<title>`

- **Status:** draft | review | accepted | rejected | superseded by RFC-`<n>`
- **Author:** `[ROLE]`
- **Date:** `<YYYY-MM-DD>`
- **Reviewers:** `[ROLE]`, `[ROLE]`, `[ROLE]`
- **Related:** F-..., W-..., ADR-..., RFC-...

---

## Summary

One paragraph. What is being proposed and why now. A reader who reads only this paragraph should understand the gist.

## Motivation

What problem does this solve? Why is the current state insufficient? Cite findings (F-...) and concrete evidence — not abstract discomfort.

## Goals

What this RFC must accomplish. Measurable where possible.

- Goal 1
- Goal 2

## Non-goals

What this RFC explicitly does not attempt. Bound the scope.

- Non-goal 1
- Non-goal 2

## Proposal

The actual design. Include:

- Architecture / data flow diagram or description
- Interfaces (function signatures, message schemas, API contracts)
- State changes
- Failure modes and how they are handled
- Migration / rollout plan
- Observability (what gets logged / measured)

Be specific enough that a competent engineer could implement from this RFC alone.

## Alternatives considered

At least two real alternatives. For each:

### Alternative A: `<name>`
- How it would work
- Why we did not choose it

### Alternative B: `<name>`
- How it would work
- Why we did not choose it

"We could do nothing" is a valid alternative when relevant; address it explicitly.

## Trade-offs

What we are giving up by choosing this design. What future options this constrains.

## Risks

What could go wrong. Likelihood and impact.

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| | | | |

## Security considerations

Mandatory section. `[SEC]` reviews this before acceptance.

- Threat model: who could abuse this and how
- Secrets / keys / credentials touched
- Network surfaces opened
- Input validation / prompt injection considerations
- Blast radius if compromised

## Performance considerations

- Expected latency / throughput impact
- Memory footprint
- GPU / CPU utilization changes
- Cost implications (token spend, infra)

## Testing strategy

How we will know it works.

- Unit tests
- Integration tests
- Eval harness changes
- Manual verification steps

## Rollout plan

- Phase 1: ...
- Phase 2: ...
- Rollback procedure: ...

## Open questions

Things that need resolution before implementation.

- Q1
- Q2

---

## Decision log

Recorded after the RFC is reviewed.

| Date | Decision | By |
|---|---|---|
| | | |
