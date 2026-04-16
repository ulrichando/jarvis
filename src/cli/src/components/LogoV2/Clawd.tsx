import * as React from 'react';
import { Box, Text } from '../../ink.js';

export type ClawdPose = 'default' | 'arms-up' | 'look-left' | 'look-right';

type Props = {
  pose?: ClawdPose;
};

// Jarvis motor logo — simple engine block rendered with Unicode block chars.
// 4 rows, ~9 cols wide. Pose is accepted for API compat but ignored (static logo).
export function Clawd({ pose: _pose = 'default' }: Props = {}): React.ReactNode {
  return (
    <Box flexDirection="column">
      <Text color="clawd_body">{" "}╔═══╗</Text>
      <Text color="clawd_body">▐█▀█▀█▌</Text>
      <Text color="clawd_body">▐█▄█▄█▌</Text>
      <Text color="clawd_body">{" "}╚═══╝</Text>
    </Box>
  );
}
