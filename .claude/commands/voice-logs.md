---
description: Tail jarvis-voice-agent.log with JSON parsing — show recent ERROR/WARNING entries
argument-hint: [N=20]
---

Tail the last N lines (default 20) of `~/.local/share/jarvis/logs/voice-agent.log`, filter to ERROR + WARNING level, and pretty-print: timestamp, level, first 160 chars of message.

```bash
N="${1:-20}"
LOG="$HOME/.local/share/jarvis/logs/voice-agent.log"
grep -E '"level": "(ERROR|WARNING)"' "$LOG" | tail -"$N" | python3 -c '
import sys, json
for line in sys.stdin:
    try:
        d = json.loads(line)
        print(d.get("timestamp","")[:19], d.get("level",""), d.get("message","")[:160])
    except Exception:
        pass
'
```

For older history use the rotated archives: `zgrep ... ~/.local/share/jarvis/logs/voice-agent.log.*.gz`. If the user asks for ALL log levels, omit the grep step. Application JSON logs go to the file path above, NOT systemd journal.
