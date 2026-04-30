# Adding a New JARVIS Sub-Agent (Specialist)

Goal: adding a specialist should be ~30 lines in one file. No edits to
`JarvisAgent`, no new entries in `JARVIS_INSTRUCTIONS`, no manual
handoff plumbing.

## Steps

1. **Create the spec module** — copy `desktop.py` as a template.

   ```python
   # src/voice-agent/specialists/research.py
   from .registry import SpecialistSpec, register


   RESEARCH_INSTRUCTIONS = """\
   You are JARVIS's research specialist. Use web_search and read_url to
   gather information, then summarize in one short paragraph and call
   task_done(summary). Don't engage in conversation — if the user
   changes topic, call task_done immediately."""


   def _research_tools() -> list:
       """Lazy import keeps livekit + langchain off the registry critical path."""
       from jarvis_research_tools import web_search, read_url
       return [web_search, read_url]


   def register_research() -> None:
       register(SpecialistSpec(
           name="research",
           transfer_tool="transfer_to_research",
           when_to_use=(
               "Use for multi-step web research: comparing prices, "
               "checking news, gathering quotes from multiple sources, "
               "fact-checking a claim."
           ),
           instructions=RESEARCH_INSTRUCTIONS,
           tool_factory=_research_tools,
           ack_phrase="Looking into it, sir.",
       ))
   ```

2. **Auto-register on package import** — add one line to
   `specialists/__init__.py::_register_builtins`:

   ```python
   def _register_builtins() -> None:
       from . import desktop, research
       desktop.register_desktop()
       research.register_research()
   ```

3. **Wire up at agent startup** — add the auto-built transfer tools
   to JarvisAgent's `tools=[…]` list:

   ```python
   from specialists.agent import build_all_transfer_tools

   _jarvis_agent = JarvisAgent(
       instructions=...,
       chat_ctx=...,
       tools=[
           # ...read-only tools...
           *build_all_transfer_tools(supervisor_self),
       ],
   )
   ```

   Note: `build_all_transfer_tools` only returns tools for `enabled=True`
   specs. Disabling a specialist is a one-line config change.

That's it. No JARVIS_INSTRUCTIONS edits — the LLM gets the routing
guidance from the spec's `when_to_use` field, which becomes the
function_tool's docstring.

## Field cheat sheet

| Field | Required | Purpose |
|---|---|---|
| `name` | yes | short identifier (`desktop`, `browser`, `planner`) |
| `transfer_tool` | yes | function_tool name (`transfer_to_<name>` by convention) |
| `when_to_use` | yes | one-line description; the LLM reads this to route |
| `instructions` | yes | the specialist's system prompt — keep ~100 lines, focused |
| `tool_factory` | yes | lazy callable returning the specialist's `@function_tool` list |
| `ack_phrase` | no | brief voiced ack on handoff (default: "On it, sir.") |
| `max_history_items` | no | chat_ctx items carried into the specialist (default: 12) |
| `enabled` | no | gate for hot-disabling without unregistering (default: True) |

## Anti-patterns

- **Don't put livekit / langchain imports at module top-level** — they
  load at registry-import time and slow startup. Use `tool_factory` for
  anything heavy.
- **Don't register from `JarvisAgent.__init__`** — registration happens
  at *module* import time so `all_specs()` is stable by the time the
  supervisor builds its tool list.
- **Don't reach into `_REGISTRY` directly** — use `register/get/all_specs`.
- **Don't write a >200-line `instructions` block** — long prompts
  degrade the specialist's tool-call discipline (verified failure mode
  during the `gpt-oss-120b` regression on 2026-04-30). Trim aggressively.
- **Don't share state between specialists** — handoff only carries chat
  history. Persistent state goes in SQLite or session attrs.

## Adding more than two

When the registry holds 4+ specialists, the supervisor's prompt should
also list them in the `TOOL ROUTING` block — but only as concise
bullets pointing at the `when_to_use` text. The function_tool docstring
is the canonical source; the prompt is just a redundant table of
contents to help the LLM remember they exist.
