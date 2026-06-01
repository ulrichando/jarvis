# browser-use Agent param surface — reliability-wiring GATE

**Authoritative for Task 3** of the Web-Nav Phase 1 plan
(`docs/superpowers/plans/2026-05-30-web-nav-p1-routing-reliability-observability.md`).
Nothing downstream (`runner.py`, `browser.py`) may pass an `Agent` kwarg that is
NOT marked present below. Trust this package introspection, not the (newer) docs.

## Installed version

> **NOTE — version discrepancy.** The plan/spec and this filename say "0.12.6".
> The actually-installed package in `~/.jarvis/browser-use-venv` is **0.12.9**
> (confirmed via `importlib.metadata.version("browser-use")` and `pip show
> browser-use`; `browser_use.__version__` reports `"?"`). Filename kept as
> `PARAMS_0_12_6.md` because the plan's Task 3 reads that path, but the binding
> fact is the introspected param surface of **0.12.9** recorded below.

```
browser_use version: ?            # browser_use.__version__ attribute
installed dist:       0.12.9       # importlib.metadata + pip show browser-use
venv:                 ~/.jarvis/browser-use-venv/bin/python
```

## Introspection (plan Step 1.1, run verbatim)

Script: `inspect.signature(Agent.__init__)` membership against the wanted set.

```json
{
  "use_vision": true,
  "max_failures": true,
  "llm_timeout": true,
  "step_timeout": true,
  "fallback_llm": true,
  "calculate_cost": true,
  "sensitive_data": true,
  "allowed_domains": false,
  "max_steps": false,
  "step_timeout_seconds": false
}
```

```
Agent.run params: ['self', 'max_steps', 'on_step_start', 'on_step_end']
```

## Where `max_steps` lives

**`Agent.run(max_steps=...)`** — NOT an `Agent.__init__` arg.

```
Agent.run.max_steps : int, default 500
```

Task 3 must pass `max_steps` to **`Agent.run(...)`**, not the `Agent(...)`
constructor.

## Present `__init__` params — exact shapes/defaults (use ONLY these)

| Param            | Annotation                                   | Default | Task-3 use |
|------------------|----------------------------------------------|---------|------------|
| `use_vision`     | `Union[bool, Literal['auto']]`               | `True`  | pass `use_vision='auto'` (valid — `'auto'` is in the Literal) |
| `max_failures`   | `int`                                        | `5`     | pass small int, e.g. `max_failures=3` |
| `llm_timeout`    | `int \| None`                                | `None`  | timeout param (per-LLM-call, seconds) |
| `step_timeout`   | `int`                                        | `180`   | timeout param (per-step, seconds) — **this is the 0.12.x name**, NOT `step_timeout_seconds` |
| `fallback_llm`   | `browser_use.llm.base.BaseChatModel \| None` | `None`  | pass a `BaseChatModel` instance (next available provider after primary) |
| `calculate_cost` | `bool`                                       | `False` | pass `calculate_cost=True` |
| `sensitive_data` | `dict[str, str \| dict[str, str]] \| None`   | `None`  | present (P3 use, not P1) |

## ABSENT params — do NOT pass; equivalents

- **`allowed_domains`** — NOT an `Agent.__init__` arg in 0.12.9, and there is NO
  domain-like param on `Agent.__init__` (`[p for p in init if 'domain' in p]` is
  empty). In browser-use 0.12.x this lives on the **browser profile / session**
  (`BrowserProfile(allowed_domains=...)`), not on `Agent`. **SKIP** for Task 3;
  revisit in P3 security via the profile path.
- **`max_steps`** — absent on `__init__`; it is an **`Agent.run(max_steps=...)`**
  arg (see above). Task 3 passes it there.
- **`step_timeout_seconds`** — does NOT exist. The 0.12.9 equivalent is
  **`step_timeout`** (`int`, seconds, default 180). Use `step_timeout`.

## Net guidance for Task 3 (runner.py Agent construction)

- `Agent(...)` kwargs that ARE safe to wire: `use_vision='auto'`,
  `max_failures=<3>`, `llm_timeout=<int|None>`, `step_timeout=<int>`,
  `fallback_llm=<BaseChatModel>`, `calculate_cost=True`.
- `max_steps` goes to **`Agent.run(max_steps=_adaptive_max_steps(...))`**.
- Do NOT pass `allowed_domains` or `step_timeout_seconds` to `Agent(...)`.
