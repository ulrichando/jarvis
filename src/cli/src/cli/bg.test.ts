import { describe, expect, test } from 'bun:test'
import { bgIdentifier } from './bg.js'

describe('bgIdentifier — first non-flag token is the session id', () => {
  test('a bare session name/id passes through', () => {
    expect(bgIdentifier(['my-session'])).toBe('my-session')
    expect(bgIdentifier(['12345'])).toBe('12345')
  })
  test('launcher-appended flags after the subcommand are skipped', () => {
    // The regression: start.sh appends `--permission-mode <mode> --settings <p>`
    // after `logs`; args[1] used to be read as the id → "No session matching
    // '--permission-mode'". The id must be the first NON-flag token.
    expect(bgIdentifier(['--permission-mode', 'bypassPermissions', '--settings', '/p'])).toBeUndefined()
    expect(bgIdentifier(['my-session', '--permission-mode', 'bypassPermissions'])).toBe('my-session')
    expect(bgIdentifier(['--settings', '/p', 'my-session'])).toBe('my-session')
  })
  test('empty args → undefined (list live sessions)', () => {
    expect(bgIdentifier([])).toBeUndefined()
  })
})
