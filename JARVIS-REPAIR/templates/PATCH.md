# Patch: W-`<n>` — `<title>`

- **Author:** `[ROLE]`
- **Resolves:** F-..., F-...
- **Reviewer:** `[REVIEWER]` (mandatory) `[SEC]` (if applicable)
- **Date:** `<YYYY-MM-DD>`

---

## Diff

```diff
<unified diff or full file content for new files>
```

## Tests

Tests added or modified. Each should fail without the fix and pass with it (where applicable).

```python
# tests/...
<test code>
```

## Test results

- [ ] All declared tests pass locally
- [ ] Existing test suite still passes
- [ ] Coverage on changed lines: `<X>%`
- [ ] Lint: clean
- [ ] Type check: clean

## Changelog

3 lines max. Imperative voice. What changed, why, and any visible behavior shift.

- `<line 1>`
- `<line 2>`
- `<line 3>`

## Self-review against Charter §4

Tick each that applies; for any not ticked, explain.

- [ ] Read all touched files end-to-end before changes
- [ ] No fabricated APIs / imports / config keys
- [ ] Patch is ≤200 lines and ≤3 files (or re-plan was triggered)
- [ ] Tests travel with the change
- [ ] Observability: new code paths emit structured logs with correlation ID
- [ ] Reversibility: rollback is documented below
- [ ] No new secrets, keys, or PII in code or logs
- [ ] Comments explain *why*, names are precise, no commented-out dead code
- [ ] Honest uncertainty noted where present

## Rollback

How to undo this change in concrete steps:

1. ...
2. ...

## Risk assessment

- **Blast radius if this is wrong:** ...
- **Detection plan** (how we'd know it broke something): ...
- **Manual verification steps before declaring Done:** ...

## Reviewer notes

`[REVIEWER]` fills in:

- [ ] Approved
- [ ] Rejected — see objections below

### Objections (if rejected)

1. ...
2. ...

`[SEC]` fills in (if applicable):

- [ ] Approved — surface area reviewed
- [ ] Rejected — see objections below

### Security objections (if rejected)

1. ...
