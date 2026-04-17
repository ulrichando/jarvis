import * as React from 'react'
import { Box, Text } from '../../ink.js'
import { env } from '../../utils/env.js'

export type ClawdPose = 'default' | 'arms-up' | 'look-left' | 'look-right'

type Props = {
  pose?: ClawdPose
}

// Jarvis logo — stylized visor/helmet rendered with Unicode block chars.
// Supports poses for animation compatibility. 9 cols wide.
//
// arms-up: the row-2 arm shapes move to row 1 as their
// bottom-heavy mirrors — same silhouette, one row higher.
//
// look-* use quadrant eye chars so both eyes change from the
// default — otherwise only one eye would appear to move.
type Segments = {
  /** row 1 left (no bg): optional raised arm + side */
  r1L: string
  /** row 1 eyes (with bg): left-eye, forehead, right-eye */
  r1E: string
  /** row 1 right (no bg): side + optional raised arm */
  r1R: string
  /** row 2 left (no bg): arm + body curve */
  r2L: string
  /** row 2 right (no bg): body curve + arm */
  r2R: string
}

const POSES: Record<ClawdPose, Segments> = {
  default: {
    r1L: ' \u2590',
    r1E: '\u259B\u2588\u2588\u2588\u259C',
    r1R: '\u258C',
    r2L: '\u259D\u259C',
    r2R: '\u259B\u2598',
  },
  'look-left': {
    r1L: ' \u2590',
    r1E: '\u259F\u2588\u2588\u2588\u259F',
    r1R: '\u258C',
    r2L: '\u259D\u259C',
    r2R: '\u259B\u2598',
  },
  'look-right': {
    r1L: ' \u2590',
    r1E: '\u2599\u2588\u2588\u2588\u2599',
    r1R: '\u258C',
    r2L: '\u259D\u259C',
    r2R: '\u259B\u2598',
  },
  'arms-up': {
    r1L: '\u2597\u259F',
    r1E: '\u259B\u2588\u2588\u2588\u259C',
    r1R: '\u2599\u2596',
    r2L: ' \u259C',
    r2R: '\u259B ',
  },
}

// Apple Terminal uses a bg-fill trick, so only eye poses make
// sense. Arm poses fall back to default.
const APPLE_EYES: Record<ClawdPose, string> = {
  default: ' \u2597   \u2596 ',
  'look-left': ' \u2598   \u2598 ',
  'look-right': ' \u259D   \u259D ',
  'arms-up': ' \u2597   \u2596 ',
}

export function Clawd({ pose = 'default' }: Props = {}): React.ReactNode {
  if (env.terminal === 'Apple_Terminal') {
    return <AppleTerminalClawd pose={pose} />
  }

  const p = POSES[pose]
  return (
    <Box flexDirection="column">
      <Text>
        <Text color="clawd_body">{p.r1L}</Text>
        <Text color="clawd_body" backgroundColor="clawd_background">
          {p.r1E}
        </Text>
        <Text color="clawd_body">{p.r1R}</Text>
      </Text>
      <Text>
        <Text color="clawd_body">{p.r2L}</Text>
        <Text color="clawd_body" backgroundColor="clawd_background">
          {'\u2588\u2588\u2588\u2588\u2588'}
        </Text>
        <Text color="clawd_body">{p.r2R}</Text>
      </Text>
      <Text color="clawd_body">
        {'  '}{'\u2598\u2598'} {'\u259D\u259D'}{'  '}
      </Text>
    </Box>
  )
}

function AppleTerminalClawd({ pose }: { pose: ClawdPose }): React.ReactNode {
  return (
    <Box flexDirection="column" alignItems="center">
      <Text>
        <Text color="clawd_body">{'\u2597'}</Text>
        <Text color="clawd_background" backgroundColor="clawd_body">
          {APPLE_EYES[pose]}
        </Text>
        <Text color="clawd_body">{'\u2596'}</Text>
      </Text>
      <Text backgroundColor="clawd_body">{' '.repeat(7)}</Text>
      <Text color="clawd_body">{'\u2598\u2598'} {'\u259D\u259D'}</Text>
    </Box>
  )
}
