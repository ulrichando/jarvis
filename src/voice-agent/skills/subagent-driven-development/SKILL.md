---
name: subagent-driven-development
description: "Execute plans by dispatching tasks to run_jarvis_cli (2-stage review)."
version: 2.0.0
author: JARVIS
license: MIT
platforms: [linux, macos, windows]
metadata:
  jarvis:
    tags: [delegation, implementation, workflow, parallel, run_jarvis_cli]
    related_skills: [writing-plans, requesting-code-review, test-driven-development]
---

# Subagent-Driven Development

## Overview

Execute implementation plans by dispatching each task to `run_jarvis_cli` with a
fresh, focused prompt and verifying the result with a two-stage review (spec then
quality) before moving to the next task.

**Core principle:** Fresh context per task + two-stage review = high quality, fast iteration.

## When to Use

Use this skill when:
- You have an implementation plan (from the `writing-plans` skill or user requirements)
- Tasks are mostly independent
- Quality and spec compliance are important
- You want systematic review between tasks

**vs. manual execution:**
- Fresh context per task — no confusion from accumulated state
- Automated review catches issues early
- Consistent quality checks across all tasks

## The Process

### 1. Read and Parse Plan

Read the plan file. Extract ALL tasks with their full text and context upfront:

```python
read_file("docs/plans/feature-plan.md")
```

**Key:** Extract all task details upfront. Provide complete task text directly in each
`run_jarvis_cli` call — don't make it re-read the plan file.

### 2. Per-Task Workflow

For EACH task in the plan:

#### Step 1: Dispatch Implementer via run_jarvis_cli

```python
run_jarvis_cli("""
Implement Task 1: Create User model with email and password_hash fields.

TASK:
- Create: src/models/user.py
- Add User class with email (str) and password_hash (str) fields
- Use bcrypt for password hashing
- Include __repr__ for debugging

FOLLOW TDD:
1. Write failing test in tests/models/test_user.py
2. Run: pytest tests/models/test_user.py -v  (verify FAIL)
3. Write minimal implementation
4. Run: pytest tests/models/test_user.py -v  (verify PASS)
5. Run: pytest tests/ -q  (verify no regressions)
6. Commit: git add tests/models/test_user.py src/models/user.py && git commit -m "feat: add User model"

PROJECT CONTEXT:
- Python 3.11, Flask app in src/app.py
- Existing models in src/models/
- Tests use pytest, run from project root
- bcrypt already in requirements.txt
""")
```

#### Step 2: Spec Compliance Review

After the implementer completes, verify against the original spec:

```python
run_jarvis_cli("""
Review: does the implementation match the spec?

ORIGINAL SPEC:
- Create src/models/user.py with User class
- Fields: email (str), password_hash (str)
- Use bcrypt for password hashing
- Include __repr__

CHECK (read the actual files):
- All requirements implemented?
- File paths match spec?
- Function signatures match spec?
- Behavior matches expected?
- No scope creep?

Output: PASS or a specific list of gaps to fix.
""")
```

**If spec issues found:** fix gaps, then re-run spec review. Continue only when spec-compliant.

#### Step 3: Code Quality Review

After spec compliance passes:

```python
run_jarvis_cli("""
Review code quality for Task 1.

FILES TO REVIEW:
- src/models/user.py
- tests/models/test_user.py

CHECK:
- Follows project conventions and style?
- Proper error handling?
- Clear variable/function names?
- Adequate test coverage?
- No obvious bugs or missed edge cases?
- No security issues?

OUTPUT:
- Critical Issues: [must fix before proceeding]
- Important Issues: [should fix]
- Minor Issues: [optional]
- Verdict: APPROVED or REQUEST_CHANGES
""")
```

**If quality issues found:** fix issues, re-review. Continue only when approved.

### 3. Final Integration Review

After ALL tasks are complete:

```python
run_jarvis_cli("""
Review the entire implementation for consistency and integration issues.

All tasks from the plan are complete. Check:
- Do all components work together?
- Any inconsistencies between tasks?
- All tests passing? (run pytest tests/ -q)
- Ready for merge?
""")
```

### 4. Verify and Commit

```bash
# Run full test suite
pytest tests/ -q

# Review all changes
git diff --stat

# Final commit if needed
git add src/ tests/ && git commit -m "feat: complete [feature name] implementation"
```

## Task Granularity

**Each task = 2–5 minutes of focused work.**

**Too big:**
- "Implement user authentication system"

**Right size:**
- "Create User model with email and password fields"
- "Add password hashing function"
- "Create login endpoint"

## Red Flags — Never Do These

- Start implementation without a plan
- Skip reviews (spec compliance OR code quality)
- Proceed with unfixed critical/important issues
- Provide incomplete context in the run_jarvis_cli call
- Accept "close enough" on spec compliance
- Move to next task while either review has open issues
- Start code quality review before spec compliance is PASS

## Handling Issues

### If run_jarvis_cli Output Shows Questions

- Answer clearly in the next run_jarvis_cli call with the full context
- Don't rush past open questions

### If Reviewer Finds Issues

- Use run_jarvis_cli to fix them with specific instructions
- Re-run the reviewer pass
- Repeat until approved

## Why run_jarvis_cli Per Task

- Fresh context per invocation — no confusion from prior task code
- Focused, clean prompt leads to better output
- Errors are isolated — one failing task doesn't poison the next

## Integration with Other Skills

### With writing-plans

This skill EXECUTES plans created by the `writing-plans` skill:
1. User requirements → `writing-plans` → implementation plan
2. Implementation plan → `subagent-driven-development` → working code

### With test-driven-development

Each `run_jarvis_cli` implementer call should include TDD instructions:
1. Write failing test first
2. Implement minimal code
3. Verify test passes
4. Commit

### With requesting-code-review

The two-stage review process IS the code review. For final integration review,
use the `requesting-code-review` skill's review dimensions.

### With systematic-debugging

If run_jarvis_cli encounters bugs during implementation:
1. Follow `systematic-debugging` process
2. Find root cause before fixing
3. Write regression test
4. Resume implementation

## Remember

```
run_jarvis_cli per task — fresh context
Two-stage review every time — spec FIRST, quality SECOND
Never skip reviews
Catch issues early
```

**Quality is not an accident. It's the result of systematic process.**
