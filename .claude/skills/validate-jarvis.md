---
name: validate-jarvis
description: Validate JARVIS brain integrity — commands, providers, memory, imports
user_invocable: true
---

Validate that the JARVIS brain is structurally healthy:

1. Run: `cd /home/ulrich/Documents/Projects/jarvis && python -c "from src.commands import registry; cmds = registry.list_commands(include_hidden=True); print(f'{len(cmds)} commands registered'); [print(f'  MISSING HANDLER: {c.name}') for c in cmds if not c.handler]"`
2. Run: `cd /home/ulrich/Documents/Projects/jarvis && python -c "from src.brain import Brain; b = Brain(); print(f'Brain OK — {len(b.command_registry.list_commands())} commands, providers: {[p[\"name\"] for p in b.reasoner.providers.list_providers()]}')" 2>&1 | tail -5`
3. Run: `cd /home/ulrich/Documents/Projects/jarvis && python -c "from src.memory.store import MemoryStore; m = MemoryStore(); s = m.stats; print(f'Memory OK — {s}')" 2>&1`
4. Check for import errors: `cd /home/ulrich/Documents/Projects/jarvis && python -c "import src.brain; import src.agent; import src.commands; import src.memory; import src.reasoning; import src.vision; print('All imports OK')" 2>&1`

Report any issues found. If everything is healthy, confirm with counts.
