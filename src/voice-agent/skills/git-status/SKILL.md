---
name: git-status
description: Summarize the current git repo state in one short sentence
when_to_use: |
  User asks "what's the state of the repo?", "any uncommitted changes?",
  "what branch am I on?", "how far ahead of main am I?", or any
  similar quick git-state question.
---

# Git Status Skill

The user wants a quick read on the current git repository state.
Run these commands via the `bash` tool and summarize in one voice-
friendly sentence.

## Recipe

1. **Identify the cwd** — `bash("pwd")` to know where we are.
2. **Branch + status** — `bash("git status --short --branch 2>&1 | head -20")`.
   The first line is `## <branch>...<remote> [ahead/behind]`. The rest
   are modified/untracked files (M / A / D / ??).
3. **Recent commits** — `bash("git log --oneline -3 2>&1")` for context
   on what was just done.

## Voice-out shape

Compose ONE sentence with the essentials only:
  - Branch name
  - Ahead/behind state if non-zero
  - "clean" if no uncommitted changes, else the count of changed files

**Examples:**

  ✅ "On feat/screen-share, 3 ahead of main, 5 files modified."
  ✅ "On main, clean, up to date with origin."
  ✅ "On feat/x, 2 ahead, 1 behind — needs a rebase."
  ❌ "Let me check the git status." (narration — just run the tool)
  ❌ "The repository is currently on the branch named foo..." (too verbose)

## When NOT to use this skill

If the user wants to commit, push, pull, merge, or any other
write operation — that's NOT this skill. Use `bash` directly for
those (after appropriate confirmation per the persona rules).
