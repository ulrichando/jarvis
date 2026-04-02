---
name: jarvis-status
description: Check if JARVIS services are running — server, desktop, Ollama, models
user_invocable: true
---

Check the operational status of all JARVIS components:

1. **Ollama**: `systemctl status ollama 2>/dev/null | head -5; ollama list 2>/dev/null`
2. **JARVIS Web Server**: `curl -s http://localhost:8765/api/mesh/ping 2>/dev/null && echo "Web server: UP" || echo "Web server: DOWN"`
3. **JARVIS Desktop**: `pgrep -fa "desktop/app.py\|jarvis.*desktop" 2>/dev/null | head -3 || echo "Desktop: not running"`
4. **JARVIS CLI**: `pgrep -fa "jarvis_cli\|shells.cli" 2>/dev/null | head -3 || echo "CLI: not running"`
5. **Provider health**: `cd /home/ulrich/Documents/Projects/jarvis && python -c "from brain.reasoning.providers import ProviderRegistry; r = ProviderRegistry(); [print(f'  {p[\"name\"]}: {p[\"model\"]} (priority {p.get(\"priority\",\"?\")})')  for p in r.list_providers()]" 2>&1`
6. **Disk usage**: `du -sh ~/.jarvis/ 2>/dev/null; du -sh /home/ulrich/Documents/Projects/jarvis/ 2>/dev/null`
7. **GPU/VRAM**: `nvidia-smi --query-gpu=memory.used,memory.total --format=csv,noheader 2>/dev/null || echo "No NVIDIA GPU detected"`

Report a clean status dashboard.
