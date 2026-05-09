/**
 * Pure parser for pytest -q --tb=short stdout.
 *
 * Returns the pytest summary line (the "X passed, Y failed in Zs" line at
 * the end) and, when failures are present, the first failure's traceback
 * block. Returns `null` for either field when the input doesn't contain
 * the expected structure — caller treats null as "couldn't parse, fall back
 * to raw output".
 *
 * Pure function. No I/O, no globals.
 */

export interface PytestSummary {
  summary: string | null
  firstFailure: string | null
}

// pytest's summary line shape: "X passed[, Y failed][, Z skipped]... in N.Ms"
const SUMMARY_RE =
  /(\d+ (?:passed|failed|skipped|error|errors|deselected|warning|warnings)(?:, \d+ (?:passed|failed|skipped|error|errors|deselected|warning|warnings))*) in [\d.]+s/

// Strip ANSI escape sequences (color codes, bold, etc.)
function stripAnsi(text: string): string {
  // eslint-disable-next-line no-control-regex
  return text.replace(/\x1b\[[0-9;]*m/g, '')
}

function extractSummary(lines: string[]): string | null {
  // Search from the end backwards — the summary is always the last
  // matching line in pytest's output.
  for (let i = lines.length - 1; i >= 0; i--) {
    const m = SUMMARY_RE.exec(lines[i])
    if (m) {
      return m[0]
    }
  }
  return null
}

function extractFirstFailure(lines: string[]): string | null {
  // The FAILURES (or ERRORS) section is delimited by `===` header lines.
  // Inside it, each failure is wrapped in `_____ test_name _____` separators.
  // We capture from the first `_____` separator until the NEXT `_____`
  // separator OR the next `===` block, whichever comes first.
  let inFailureSection = false
  let captureStart = -1
  let captureEnd = -1
  for (let i = 0; i < lines.length; i++) {
    const line = lines[i]
    if (/^=+ (FAILURES|ERRORS) =+$/.test(line)) {
      inFailureSection = true
      continue
    }
    if (!inFailureSection) continue
    // A `_____ ... _____` separator marks a failure boundary.
    const sepMatch = /^_+ .+ _+$/.test(line)
    if (sepMatch) {
      if (captureStart === -1) {
        captureStart = i
        continue
      }
      // Hit the next separator — first-failure ends here.
      captureEnd = i
      break
    }
    // A `===` boundary (e.g. "short test summary info") closes the failure
    // section before any second separator appeared.
    if (captureStart !== -1 && /^=+ .+ =+$/.test(line)) {
      captureEnd = i
      break
    }
  }
  if (captureStart === -1) return null
  if (captureEnd === -1) captureEnd = lines.length
  return lines.slice(captureStart, captureEnd).join('\n').trim() || null
}

export function parsePytestSummary(stdout: string): PytestSummary {
  if (!stdout) return { summary: null, firstFailure: null }
  const cleaned = stripAnsi(stdout)
  const lines = cleaned.split('\n')
  return {
    summary: extractSummary(lines),
    firstFailure: extractFirstFailure(lines),
  }
}
