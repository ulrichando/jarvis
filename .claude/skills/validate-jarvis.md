---
name: validate-jarvis
description: Validate JARVIS brain integrity — commands, providers, memory, imports
user_invocable: true
---

Validate that the JARVIS brain is structurally healthy:

1. Run: `cd /home/ulrich/Documents/Projects/jarvis && python -c "from brain.commands import registry; cmds = registry.list_commands(include_hidden=True); print(f'{len(cmds)} commands registered'); [print(f'  MISSING HANDLER: {c.name}') for c in cmds if not c.handler]"`
2. Run: `cd /home/ulrich/Documents/Projects/jarvis && python -c "from brain.main import Brain; b = Brain(); print(f'Brain OK — {len(b.command_registry.list_commands())} commands, providers: {[p[\"name\"] for p in b.reasoner.providers.list_providers()]}')" 2>&1 | tail -5`
3. Run: `cd /home/ulrich/Documents/Projects/jarvis && python -c "from brain.memory.store import MemoryStore; m = MemoryStore(); s = m.stats; print(f'Memory OK — {s}')" 2>&1`
4. Check for import errors: `cd /home/ulrich/Documents/Projects/jarvis && python -c "import brain; import brain.agent; import brain.commands; import brain.memory; import brain.reasoning; import brain.vision; print('All imports OK')" 2>&1`

Report any issues found. If everything is healthy, confirm with counts.
