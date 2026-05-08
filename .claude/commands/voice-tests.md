---
description: Run the voice-agent pytest suite (full or filtered by -k)
argument-hint: [-k filter]
---

Run pytest in [src/voice-agent/](src/voice-agent/). The voice-agent has its own `.venv` — use it directly, don't activate.

```bash
cd src/voice-agent
.venv/bin/python -m pytest tests/ "$@" --no-header -q
```

Common filters:
- `-k "router or interrupt or turn"` — interrupt tuning + turn pipeline
- `-k "specialist"` — registry, agent, gate
- `-k "sanitizer"` — pycall / dsml / handoff_text
- `-k "confab"` — confabulation detector
- `-k "memory"` — memory layer + audit_memories

Report: total pass/fail count and the first failing test's traceback (if any). Don't print the full output if all pass.
