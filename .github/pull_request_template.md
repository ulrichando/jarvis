## What & why
<!-- One or two sentences: what this changes and why it's needed. -->

## Scope
<!-- From .claude/rules/regression-prevention.md — makes "while I'm here…" edits auditable. -->
- **SCOPE:** <files / dirs intentionally in scope>
- **OUT:** <files / areas deliberately not touched>

## Verification
<!-- Evidence, not vibes. Which suite/build ran, with result. -->
- [ ] Affected tree checked: voice-agent `pytest` · web `bun run build` + `vitest` · cli `bun build` · desktop `cargo build` · android `gradlew compileDebugKotlin`
- [ ] No secrets added (push protection is on)
- [ ] PR title follows Conventional Commits (`feat:` / `fix:` / `chore:` / `ci:` / `docs:` …) — feeds release-please

## Notes
<!-- Anything reviewers should know: trade-offs, follow-ups, screenshots. -->
