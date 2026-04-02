---
name: test-jarvis
description: Run the full JARVIS test suite and report results
user_invocable: true
---

Run the JARVIS test suite and report results clearly:

1. Run `python -m pytest test/ -q --tb=short` from the project root at `/home/ulrich/Documents/Projects/jarvis/`
2. If any tests fail, read the failing test file and the code it tests to understand the failure
3. Report: total passed, total failed, and for each failure: test name, file, and root cause
4. If all tests pass, just say "All tests pass" with the count
