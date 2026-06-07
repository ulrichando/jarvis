---
description: Check voice-agent dependencies for staleness, skew, or missing packages
---

Run the dependency health check for the voice-agent venv and report findings:

```bash
bin/jarvis-dep-check status
```

If no check has run yet, run one:

```bash
bash scripts/jarvis-dep-check.sh
```

Report the status, any MISSING packages, any version SKEW between livekit-agents and its plugins, and any OUTDATED packages. If everything is clean, say so briefly. If there are issues, list them clearly with package names and versions.
