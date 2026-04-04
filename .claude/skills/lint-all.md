---
name: lint-all
description: Run Python syntax check, Rust check, and JS lint across all JARVIS code
user_invocable: true
---

Run linters across the entire JARVIS codebase:

1. **Python** — Check all .py files compile:
   ```
   cd /home/ulrich/Documents/Projects/jarvis && find src/ test/ -name "*.py" -exec python -m py_compile {} \; 2>&1 | head -20
   ```
   If py_compile shows no output, all files are syntactically valid.

2. **Rust** — Check core compiles:
   ```
   cd /home/ulrich/Documents/Projects/jarvis && cargo check 2>&1 | tail -10
   ```

3. **JavaScript/React** — Lint frontend:
   ```
   cd /home/ulrich/Documents/Projects/jarvis/shells/web/frontend && npx eslint src/ 2>&1 | tail -20
   ```

Report a summary: how many files checked per language, any errors found. {{args}}
