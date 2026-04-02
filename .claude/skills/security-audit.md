---
name: security-audit
description: Quick security audit of JARVIS codebase — secrets, permissions, exposed endpoints
user_invocable: true
---

Run a security audit on the JARVIS codebase at `/home/ulrich/Documents/Projects/jarvis/`:

1. **Hardcoded secrets**: Search for API keys, passwords, tokens in source code:
   - `grep -rn "api_key\s*=\s*['\"]" brain/ shells/ --include="*.py" | grep -v "ollama\|test\|example\|masked\|__pycache__"`
   - `grep -rn "password\s*=\s*['\"]" brain/ shells/ --include="*.py" | grep -v "test\|example\|__pycache__"`

2. **Exposed endpoints**: Check web server for unauthenticated routes:
   - Search for route definitions in `shells/web/server.py` and check if any have auth middleware

3. **Dangerous permissions**: Check for overly permissive file operations:
   - `grep -rn "subprocess\.\(call\|run\|Popen\)" brain/ --include="*.py" | grep -v "__pycache__" | head -20`
   - Check `brain/permissions.py` for current permission level

4. **Dependency vulnerabilities**: `pip audit 2>/dev/null | head -20 || echo "pip-audit not installed"`

5. **File permissions**: `find ~/.jarvis/ -perm -o+r -name "*.json" 2>/dev/null | head -10`

Report findings with severity (critical/medium/low). {{args}}