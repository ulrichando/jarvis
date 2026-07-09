import * as React from 'react'
import { useAppState } from 'src/state/AppState.js'
import { Box, Text } from '../ink.js'
import { Select } from './CustomSelect/index.js'
import { Dialog } from './design-system/Dialog.js'

// Pre-launch permission dialog for /ultraplan (slash command or keyword).
// Authored 2026-07-09: REPL.tsx has referenced this component since the fork,
// but the implementation was stripped from the public donor build — typing
// "ultraplan" crashed the whole REPL with "UltraplanLaunchDialog is not
// defined" the moment ultraplanLaunchPending was set (live user report).
//
// Contract (REPL.tsx owns the consequences): REPL clears
// ultraplanLaunchPending on ANY choice, then for a non-cancel choice echoes
// the command and calls launchUltraplan. opts.disconnectedBridge is threaded
// through for a future "disconnect remote control first" option — not offered
// yet, so it's never set here.
type Props = {
  onChoice: (
    choice: 'launch' | 'cancel',
    opts?: { disconnectedBridge?: boolean },
  ) => void
}

const MAX_BLURB_CHARS = 200

export function UltraplanLaunchDialog({ onChoice }: Props): React.ReactNode {
  const blurb = useAppState(s => s.ultraplanLaunchPending?.blurb) ?? ''
  const preview =
    blurb.length > MAX_BLURB_CHARS ? `${blurb.slice(0, MAX_BLURB_CHARS)}…` : blurb

  return (
    <Dialog title="Launch ultraplan?" onCancel={() => onChoice('cancel')}>
      <Box flexDirection="column" gap={1}>
        <Text>
          Jarvis on the web drafts an advanced multi-agent plan for your prompt
          (~10–30 min, Opus). Your terminal stays free while the remote session
          plans; the finished plan lands back here for approval.
        </Text>
        {preview ? <Text dimColor>“{preview}”</Text> : null}
      </Box>
      <Select
        options={[
          { label: 'Launch in Jarvis on the web', value: 'launch' },
          { label: 'Cancel', value: 'cancel' },
        ]}
        onChange={value => onChoice(value as 'launch' | 'cancel')}
      />
    </Dialog>
  )
}
