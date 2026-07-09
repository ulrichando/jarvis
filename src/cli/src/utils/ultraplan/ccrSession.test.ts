// Scanner tests for the /ultraplan CCR event stream — the teleport-back leg.
// Event shapes mirror what the jarvis-web bridge persists in session_events
// (worker/events route) and serves to pollRemoteSessionEvents:
//   - assistant message with an ExitPlanMode tool_use (the plan pause)
//   - user message with the deny tool_result the container CLI synthesizes
//     when the /plan route resolves the permission (is_error + content =
//     "__ULTRAPLAN_TELEPORT_LOCAL__\n<plan>" for teleport-local).
// Live-captured 2026-07-09 via a real CLI run in SDK stdio mode.
import { describe, expect, test } from 'bun:test'
import type { SDKMessage } from '../../entrypoints/agentSdkTypes.js'
import {
  ExitPlanModeScanner,
  ULTRAPLAN_TELEPORT_SENTINEL,
} from './ccrSession.js'

function exitPlanCall(id: string): SDKMessage {
  return {
    type: 'assistant',
    message: {
      role: 'assistant',
      content: [{ type: 'tool_use', id, name: 'ExitPlanMode', input: {} }],
    },
    session_id: 's1',
  } as unknown as SDKMessage
}

function toolResult(
  id: string,
  content: unknown,
  isError: boolean,
): SDKMessage {
  return {
    type: 'user',
    message: {
      role: 'user',
      content: [
        { type: 'tool_result', tool_use_id: id, content, is_error: isError },
      ],
    },
    session_id: 's1',
  } as unknown as SDKMessage
}

const teleportContent = (plan: string): string =>
  `${ULTRAPLAN_TELEPORT_SENTINEL}\n${plan}`

describe('ExitPlanModeScanner teleport detection', () => {
  test('deny tool_result with the sentinel → teleport with the plan text', () => {
    const scanner = new ExitPlanModeScanner()
    expect(scanner.ingest([exitPlanCall('t1')])).toEqual({ kind: 'pending' })
    expect(
      scanner.ingest([toolResult('t1', teleportContent('PLAN BODY'), true)]),
    ).toEqual({ kind: 'teleport', plan: 'PLAN BODY' })
  })

  test('sentinel in array-of-text-blocks content (threadstore encoding) → teleport', () => {
    const scanner = new ExitPlanModeScanner()
    scanner.ingest([exitPlanCall('t1')])
    expect(
      scanner.ingest([
        toolResult('t1', [{ type: 'text', text: teleportContent('P') }], true),
      ]),
    ).toEqual({ kind: 'teleport', plan: 'P' })
  })

  // THE 2026-07-09 regression: the remote agent reacts to the teleport deny by
  // re-calling ExitPlanMode within the poll window (live capture: 3.5s), so one
  // ingest batch contains BOTH the sentinel tool_result on the old call AND a
  // newer pending call. The newest-first scan alone returns 'pending' forever
  // — the new call waits on a browser decision the user already made — and the
  // plan never returns to the terminal.
  test('teleport tool_result shadowed by a same-batch ExitPlanMode re-call still wins', () => {
    const scanner = new ExitPlanModeScanner()
    scanner.ingest([exitPlanCall('t1')])
    expect(
      scanner.ingest([
        toolResult('t1', teleportContent('PLAN BODY'), true),
        exitPlanCall('t2'), // the re-call — pending, would shadow t1
      ]),
    ).toEqual({ kind: 'teleport', plan: 'PLAN BODY' })
  })

  test('teleport re-call arriving in a LATER batch than the sentinel also wins', () => {
    const scanner = new ExitPlanModeScanner()
    scanner.ingest([exitPlanCall('t1'), exitPlanCall('t2')])
    // t2 pending; t1's sentinel lands after — the override must still find it
    expect(
      scanner.ingest([toolResult('t1', teleportContent('X'), true)]),
    ).toEqual({ kind: 'teleport', plan: 'X' })
  })

  test('plain rejection + newer pending call stays pending (revise flow intact)', () => {
    const scanner = new ExitPlanModeScanner()
    scanner.ingest([exitPlanCall('t1')])
    expect(
      scanner.ingest([
        toolResult('t1', 'Plan rejected by user.', true),
        exitPlanCall('t2'),
      ]),
    ).toEqual({ kind: 'pending' })
    expect(scanner.rejectCount).toBe(0) // t2 pending — t1 never became the scan target
  })

  test('plain rejection alone → rejected, then next plan pauses again', () => {
    const scanner = new ExitPlanModeScanner()
    scanner.ingest([exitPlanCall('t1')])
    expect(
      scanner.ingest([toolResult('t1', 'Plan rejected by user.', true)]),
    ).toEqual({ kind: 'rejected', id: 't1' })
    expect(scanner.ingest([exitPlanCall('t2')])).toEqual({ kind: 'pending' })
    expect(scanner.rejectCount).toBe(1)
  })

  test('approved tool_result → approved with the plan text', () => {
    const scanner = new ExitPlanModeScanner()
    scanner.ingest([exitPlanCall('t1')])
    expect(
      scanner.ingest([toolResult('t1', '## Approved Plan:\nDO X', false)]),
    ).toEqual({ kind: 'approved', plan: 'DO X' })
  })

  // The deny+interrupt flow (web /plan `local` branch) ends the remote turn
  // with result subtype error_during_execution right after the sentinel
  // tool_result (live capture 2026-07-09). Teleport must win over terminated
  // when both land in one batch — same precedence as approved-over-terminated.
  test('sentinel + interrupt-produced error result in one batch → teleport, not terminated', () => {
    const scanner = new ExitPlanModeScanner()
    scanner.ingest([exitPlanCall('t1')])
    expect(
      scanner.ingest([
        toolResult('t1', teleportContent('PLAN'), true),
        {
          type: 'result',
          subtype: 'error_during_execution',
          session_id: 's1',
        } as unknown as SDKMessage,
      ]),
    ).toEqual({ kind: 'teleport', plan: 'PLAN' })
  })
})
