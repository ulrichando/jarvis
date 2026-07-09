import { describe, expect, test } from 'bun:test'

import { redactSecrets } from './debugUtils.js'

describe('redactSecrets', () => {
  test('token-refresh stdin frame: the session bearer never survives into the log', () => {
    // Exact shape sessionRunner.updateAccessToken writes to the child's stdin
    // (and echoes to the debug log via writeStdin).
    const token = 'sk-ant-oat01-AAAABBBBCCCCDDDDEEEEFFFF0123456789'
    const frame = JSON.stringify({
      type: 'update_environment_variables',
      variables: { CLAUDE_CODE_SESSION_ACCESS_TOKEN: token },
    })
    const out = redactSecrets(frame)
    expect(out).not.toContain(token)
    // Long values keep the head/tail hint shape.
    expect(out).toContain(`"CLAUDE_CODE_SESSION_ACCESS_TOKEN":"${token.slice(0, 8)}...${token.slice(-4)}"`)
  })

  test('short secrets are fully redacted; non-secret fields untouched', () => {
    const out = redactSecrets('{"token":"abc","title":"hello"}')
    expect(out).toContain('"token":"[REDACTED]"')
    expect(out).toContain('"title":"hello"')
  })
})
