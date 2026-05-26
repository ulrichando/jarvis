import * as React from 'react'
import { Box, Text } from '../../ink.js'
import { env } from '../../utils/env.js'

export type ClawdPose = 'default' | 'arms-up' | 'look-left' | 'look-right'

type Props = {
  pose?: ClawdPose
}

// JARVIS robot-face logo: rounded helmet with two glowing eye sensors. Pose API kept for AnimatedClawd compatibility — face is symmetric so all 4 poses render identically.
type Segments = {
  /** row 1 left (no bg) */
  r1L: string
  /** row 1 inner (with bg = glow) */
  r1E: string
  /** row 1 right (no bg) */
  r1R: string
  /** row 2 left (no bg) */
  r2L: string
  /** row 2 right (no bg) */
  r2R: string
  /** row 3 left (no bg) */
  r3L: string
  /** row 3 inner (with bg = glow) */
  r3E: string
  /** row 3 right (no bg) */
  r3R: string
}

const ROBOT_FACE: Segments = {
  r1L: ' \u2584',
  r1E: '\u2580\u2580\u2580\u2580\u2580',
  r1R: '\u2584 ',
  r2L: ' \u258c',
  r2R: '\u2590 ',
  r3L: ' \u2580',
  r3E: '\u2584\u2584\u2584\u2584\u2584',
  r3R: '\u2580 ',
}

const POSES: Record<ClawdPose, Segments> = {
  default: ROBOT_FACE,
  'look-left': ROBOT_FACE,
  'look-right': ROBOT_FACE,
  'arms-up': ROBOT_FACE,
}

const APPLE_CORE: Record<ClawdPose, string> = {
  default: '\u25C9       \u25C9',
  'look-left': '\u25C9       \u25C9',
  'look-right': '\u25C9       \u25C9',
  'arms-up': '\u25C9       \u25C9',
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
          {'\u25C9   \u25C9'}
        </Text>
        <Text color="clawd_body">{p.r2R}</Text>
      </Text>
      <Text>
        <Text color="clawd_body">{p.r3L}</Text>
        <Text color="clawd_body" backgroundColor="clawd_background">
          {p.r3E}
        </Text>
        <Text color="clawd_body">{p.r3R}</Text>
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
          {APPLE_CORE[pose]}
        </Text>
        <Text color="clawd_body">{'\u2596'}</Text>
      </Text>
      <Text backgroundColor="clawd_body">{' '.repeat(7)}</Text>
      <Text color="clawd_body">{'\u2598\u2598'} {'\u259D\u259D'}</Text>
    </Box>
  )
}
