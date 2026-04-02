---
name: build-frontend
description: Build the JARVIS React frontend and verify the output
user_invocable: true
---

Build the JARVIS web frontend:

1. `cd /home/ulrich/Documents/Projects/jarvis/shells/web/frontend && npm install 2>&1 | tail -3`
2. `cd /home/ulrich/Documents/Projects/jarvis/shells/web/frontend && npm run build 2>&1`
3. Verify output: `ls -la /home/ulrich/Documents/Projects/jarvis/shells/web/frontend/dist/ 2>/dev/null | head -10`
4. Check bundle size: `du -sh /home/ulrich/Documents/Projects/jarvis/shells/web/frontend/dist/ 2>/dev/null`

Report success/failure and bundle size. If build fails, read the error and suggest a fix.