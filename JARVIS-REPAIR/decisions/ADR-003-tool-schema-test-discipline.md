# ADR-003: Tool-schema-touching patches require a coverage test that walks every registered tool

- **Status:** accepted
- **Date:** 2026-05-05
- **Deciders:** `[ARCH]`, `[ORCH]`, `[QA]`
- **Consulted:** `[REVIEWER]` (POSTMORTEM-001 author)
- **Informed:** all roles

## Context

W-009 went through three iterations before producing a fix that didn't break voice. POSTMORTEM-001 traces the cause: the unit tests for each iteration asserted a schema *shape* in isolation but never tested the contract Groq actually enforces — a request-level invariant that all tools' schemas share the same mode (full strict or full legacy). Iterations 1 and 2 each passed their unit tests AND produced shapes Groq rejects in production. The user reported "JARVIS is thinking and not talking" 3.5 hours into iteration 2's deployment.

The structural pattern of the bug:

- Iteration 1's unit test asserted "`required` is partial when params have defaults." True. But it didn't assert "`additionalProperties: false` is dropped accordingly." Provider rejected.
- Iteration 2's unit test asserted "`additionalProperties: false` is dropped." True. But it didn't assert "function.strict is dropped to keep the per-tool mode consistent across the request." Provider rejected.
- Iteration 3's unit test asserts "every schema is legacy-shape." True AND the contract Groq enforces.

Each iteration's tests were locally correct. The mistake was reasoning about per-tool shape rather than the request-level contract that includes the *interaction* between tools.

The Charter §4 Principle 5 ("tests travel with code") was honored each time. The Charter §7 SLO around "prompt-injection test suite passes 100% before any prompt change ships" exists for prompts but no equivalent existed for tool schemas. POSTMORTEM-001 logged this as W-010 (build the coverage test) + W-011 (document the discipline so it persists).

## Decision

The repair effort adopts the following rule, binding for all future patches:

> **Any patch that touches a tool-schema generator, monkey-patch, or schema-shape transformation MUST include a coverage test that walks every registered specialist + subagent + supervisor tool, builds each tool's schema, and asserts the schema satisfies the live request-level contract that Groq (and any other strict-mode provider in use) enforces.**

The test lives at `src/voice-agent/tests/test_strict_schema_relax.py` today and contains:

- `test_every_registered_specialist_tool_uses_legacy_shape` — walks `all_specs() + all_subagents()`, calls each `tool_factory()`, asserts every tool's schema is legacy-shape (no `additionalProperties: false`, no `function.strict: True`).
- `test_supervisor_top_level_tools_use_legacy_shape` — names the supervisor-level @function_tool decorated functions explicitly (because there's no registry to walk for them) and applies the same assertion.

Both tests are exhaustive: the moment any tool produces a non-legacy schema, they fail. Adding a new specialist or supervisor tool can't accidentally bypass the assertions because the registry walk is dynamic.

## Consequences

### Positive

- The W-009 iteration class of regression cannot recur silently. The next time someone changes the schema generator, a single test run flags the global breakage before the patch ships.
- The contract is documented in test code — the "every schema is legacy-shape" rule is the actual current contract the production stack depends on, not a comment that decays.
- The test runs in milliseconds. There's no API cost; everything is local Pydantic + JSON-schema work.

### Negative

- The test ties production-shape coverage to the local registry. If a tool is registered through a code path the registry walk doesn't cover (a custom `function_tool` decoration somewhere unusual), the coverage test misses it. Today the `test_supervisor_top_level_tools_use_legacy_shape` test mitigates this by naming the known-out-of-registry tools explicitly — but adding a new such tool requires updating the test list. This is an acceptable maintenance cost; better than no coverage.
- The test enforces "every schema is legacy-shape" rather than a more nuanced "valid for the provider." If we later integrate a provider that requires strict-mode schemas, this rule constrains us — a follow-up ADR would reverse or generalize it.

### Neutral / follow-up needed

- The Charter §7 Operational Standards section does not explicitly call out tool-schema-test discipline. A future Charter amendment could add it under "Code quality" or "Observability." For now, this ADR + the existing tests stand as the binding contract.
- The test contract can be extended to cover other strict-mode providers (Kimi, OpenAI's GPT-4o, Anthropic's Claude). Each provider's strict-mode rules differ in detail; the legacy-shape contract works for all currently supported providers because they all accept legacy as a fallback.

## Alternatives considered

### Alternative A: Smoke-test against a real provider in CI

How it would work: send a tiny request to Groq (or a mock OpenAI-compat server) with the registered tool list and assert the request was accepted. Catches provider-specific surprises beyond what the local test models.

Why we did not choose it: API cost (every CI run hits the provider), provider availability (test fails if Groq is down), provider-mock divergence (a local mock might not enforce strict-mode the same way as Groq actually does). The local test catches the bug class POSTMORTEM-001 names; we accept residual risk for live-only failures and rely on production soak observation to catch them.

### Alternative B: Merge this rule into `test_specialists_health.py::test_all_tool_schemas_build_strict`

How it would work: add the legacy-shape assertion inline with the existing build-clean assertion.

Why we did not choose it: that test is parametrized per spec name; inlining the schema assertion would couple two unrelated checks. Keeping the W-010 test in `test_strict_schema_relax.py` (where the related sanitizer + its other tests live) is more discoverable.

### Alternative C: Don't enforce — rely on POSTMORTEM-001 + manual care

Why we did not choose it: POSTMORTEM-001 §"What went poorly" explicitly identified manual-care-as-prevention as insufficient. The same author hit the same bug class three times in the same session. Process discipline only works if the discipline is enforceable.

## Override / disagreement record

None. `[ARCH]`, `[ORCH]`, `[QA]`, `[REVIEWER]` all agreed.
