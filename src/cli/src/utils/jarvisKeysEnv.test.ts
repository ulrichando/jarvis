import { afterEach, beforeEach, describe, expect, test } from 'bun:test'

import { mkdtempSync, readFileSync, rmSync, statSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'

import {
  readKeysEnvValue,
  removeKeysEnvKeys,
  upsertKeysEnv,
} from './jarvisKeysEnv.js'

describe('jarvisKeysEnv — keys.env upsert/remove/read', () => {
  let dir: string
  let path: string

  beforeEach(() => {
    dir = mkdtempSync(join(tmpdir(), 'jarvis-keysenv-'))
    path = join(dir, 'keys.env')
  })

  afterEach(() => {
    rmSync(dir, { recursive: true, force: true })
  })

  test('creates file with 0600 and parent dir when absent', () => {
    const nested = join(dir, 'sub', 'keys.env')
    upsertKeysEnv({ JARVIS_BRIDGE_TOKEN: 'jbr_abc-123' }, nested)
    expect(readFileSync(nested, 'utf8')).toBe('JARVIS_BRIDGE_TOKEN=jbr_abc-123\n')
    expect(statSync(nested).mode & 0o777).toBe(0o600)
  })

  test('preserves comments, ordering, and unrelated keys on update', () => {
    writeFileSync(
      path,
      '# secrets\nANTHROPIC_API_KEY=sk-old\nJARVIS_BRIDGE_BASE_URL=http://old:3000\nGOOGLE_API_KEY=g-1\n',
    )
    upsertKeysEnv(
      {
        JARVIS_BRIDGE_BASE_URL: 'http://localhost:3000',
        JARVIS_BRIDGE_TOKEN: 'jbr_x',
      },
      path,
    )
    expect(readFileSync(path, 'utf8')).toBe(
      '# secrets\nANTHROPIC_API_KEY=sk-old\nJARVIS_BRIDGE_BASE_URL=http://localhost:3000\nGOOGLE_API_KEY=g-1\nJARVIS_BRIDGE_TOKEN=jbr_x\n',
    )
  })

  test('rewrites every duplicate occurrence (loaders are last-wins)', () => {
    writeFileSync(path, 'K=a\nK=b\n')
    upsertKeysEnv({ K: 'c' }, path)
    expect(readFileSync(path, 'utf8')).toBe('K=c\nK=c\n')
  })

  test('normalizes a legacy `export KEY=` line in place', () => {
    writeFileSync(path, 'export JARVIS_BRIDGE_TOKEN=jbr_old\n')
    upsertKeysEnv({ JARVIS_BRIDGE_TOKEN: 'jbr_new' }, path)
    expect(readFileSync(path, 'utf8')).toBe('JARVIS_BRIDGE_TOKEN=jbr_new\n')
  })

  test('preserves an existing non-default file mode', () => {
    writeFileSync(path, 'A=1\n', { mode: 0o640 })
    upsertKeysEnv({ A: '2' }, path)
    expect(statSync(path).mode & 0o777).toBe(0o640)
  })

  test('rejects values the consumers parse inconsistently', () => {
    expect(() => upsertKeysEnv({ A: 'has space' }, path)).toThrow(/consistently/)
    expect(() => upsertKeysEnv({ A: 'semi;colon' }, path)).toThrow()
    expect(() => upsertKeysEnv({ A: 'dollar$var' }, path)).toThrow()
    expect(() => upsertKeysEnv({ 'bad-key': 'x' }, path)).toThrow(/invalid key/)
  })

  test('removeKeysEnvKeys drops all occurrences and reports change', () => {
    writeFileSync(path, '# keep\nA=1\nJARVIS_BRIDGE_TOKEN=jbr_x\nA=2\n')
    expect(removeKeysEnvKeys(['A'], path)).toBe(true)
    expect(readFileSync(path, 'utf8')).toBe('# keep\nJARVIS_BRIDGE_TOKEN=jbr_x\n')
    expect(removeKeysEnvKeys(['A'], path)).toBe(false)
    expect(removeKeysEnvKeys(['NOPE'], join(dir, 'absent.env'))).toBe(false)
  })

  test('readKeysEnvValue returns last occurrence, handles export prefix', () => {
    writeFileSync(path, 'A=1\nexport A=2\nB=x=y\n')
    expect(readKeysEnvValue('A', path)).toBe('2')
    expect(readKeysEnvValue('B', path)).toBe('x=y')
    expect(readKeysEnvValue('C', path)).toBeUndefined()
    expect(readKeysEnvValue('A', join(dir, 'absent.env'))).toBeUndefined()
  })
})
