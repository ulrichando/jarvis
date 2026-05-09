import { describe, expect, test } from 'bun:test'
import { parsePytestSummary } from './parsePytestSummary.js'

describe('parsePytestSummary', () => {
  test('extracts summary on all-pass', () => {
    const stdout = '.....\n24 passed in 1.37s\n'
    const r = parsePytestSummary(stdout)
    expect(r.summary).toBe('24 passed in 1.37s')
    expect(r.firstFailure).toBeNull()
  })

  test('extracts summary with skipped + warnings', () => {
    const stdout = '...s..\n1057 passed, 2 skipped, 3 warnings in 17.47s\n'
    const r = parsePytestSummary(stdout)
    expect(r.summary).toBe('1057 passed, 2 skipped, 3 warnings in 17.47s')
    expect(r.firstFailure).toBeNull()
  })

  test('extracts summary + first failure block on mixed', () => {
    const stdout = [
      '.F....',
      '=================================== FAILURES ===================================',
      '______________________________ test_something _________________________________',
      'tests/test_foo.py:42: in test_something',
      '    assert x == 1',
      'E   AssertionError: assert 0 == 1',
      '______________________________ test_other _____________________________________',
      'tests/test_foo.py:99: in test_other',
      '    assert y == 2',
      'E   AssertionError: assert 0 == 2',
      '=========================== short test summary info ============================',
      'FAILED tests/test_foo.py::test_something',
      'FAILED tests/test_foo.py::test_other',
      '2 failed, 21 passed in 1.72s',
      '',
    ].join('\n')
    const r = parsePytestSummary(stdout)
    expect(r.summary).toBe('2 failed, 21 passed in 1.72s')
    expect(r.firstFailure).toContain('test_something')
    expect(r.firstFailure).toContain('AssertionError: assert 0 == 1')
    // First failure ONLY — should not include test_other.
    expect(r.firstFailure).not.toContain('test_other')
  })

  test('extracts summary on collection error', () => {
    const stdout = [
      '=================================== ERRORS ====================================',
      '________________________ ERROR collecting test_foo.py _________________________',
      "ModuleNotFoundError: No module named 'foo'",
      '=========================== short test summary info ============================',
      'ERROR tests/test_foo.py',
      '!!!!!!!!! Interrupted: 1 error during collection !!!!!!!!!',
      '1 error in 3.72s',
      '',
    ].join('\n')
    const r = parsePytestSummary(stdout)
    expect(r.summary).toBe('1 error in 3.72s')
    expect(r.firstFailure).toContain('ERROR collecting test_foo.py')
    expect(r.firstFailure).toContain("No module named 'foo'")
  })

  test('strips ANSI before parsing', () => {
    const stdout = '\x1b[32m.....\x1b[0m\n\x1b[1m24 passed in 1.37s\x1b[0m\n'
    const r = parsePytestSummary(stdout)
    expect(r.summary).toBe('24 passed in 1.37s')
  })

  test('returns nulls on empty input', () => {
    expect(parsePytestSummary('')).toEqual({ summary: null, firstFailure: null })
  })

  test('returns nulls on garbage with no summary line', () => {
    const r = parsePytestSummary('not a pytest output at all\nrandom text\n')
    expect(r.summary).toBeNull()
    expect(r.firstFailure).toBeNull()
  })

  test('picks up summary even with trailing blank lines', () => {
    const stdout = '24 passed in 1.37s\n\n\n'
    const r = parsePytestSummary(stdout)
    expect(r.summary).toBe('24 passed in 1.37s')
  })
})
