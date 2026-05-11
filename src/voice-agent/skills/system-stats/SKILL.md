---
name: system-stats
description: Summarize CPU, RAM, disk, and battery in one short voice sentence
when_to_use: |
  User asks "how's the system?", "what's my CPU usage?", "how much
  RAM am I using?", "disk space?", "battery left?", or any similar
  quick resource-state question.
---

# System Stats Skill

The user wants a quick read on system resource usage. Pick which
metrics they asked for (or all four if they said "system" generally)
and pull each via `bash`. Summarize in one short sentence.

## Recipe

**CPU usage (1-second average):**
  `bash("top -bn1 | grep '%Cpu' | awk '{print 100 - $8}'")`
  → returns e.g. "23.4"  (% used)

**RAM (used / total in GB):**
  `bash("free -h | awk '/^Mem:/ {print $3 \"/\" $2}'")`
  → returns e.g. "12Gi/31Gi"

**Disk (root fs used %):**
  `bash("df -h / | awk 'NR==2 {print $5 \" of \" $2}'")`
  → returns e.g. "67% of 512G"

**Battery (% remaining, charging state):**
  `bash("upower -i $(upower -e | grep BAT) | grep -E 'percentage|state' | awk '{print $2}' | paste -d ' ' - -")`
  → returns e.g. "78% discharging"  (no battery = empty output)

## Voice-out shape

ONE sentence. Pick the metrics the user actually asked for; default
to all four when they said "system" generally.

**Examples:**

  ✅ User: "How's the system?"
     You:  "CPU 23%, RAM 12 of 31 gigs, disk 67% full, battery 78%."

  ✅ User: "What's my CPU usage?"
     You:  "23%."

  ✅ User: "How much RAM is free?"
     You:  "About 19 of 31 gigs free."

  ❌ "Let me check the system stats for you." (narration — just run)
  ❌ A multi-line breakdown reciting each metric on its own line.

If a command fails (e.g. no battery, no upower installed), skip that
metric silently — don't apologize, just report the ones that worked.
