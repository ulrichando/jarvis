import { feature } from 'bun:bundle'
import * as React from 'react'
import { useEffect, useState } from 'react'
import { Box, Text } from '../../ink.js'
import {
  getInitialSettings,
  updateSettingsForSource,
} from '../../utils/settings/settings.js'
import { isVoiceModeEnabled } from '../../voice/voiceModeEnabled.js'
import { AnimatedAsterisk } from './AnimatedAsterisk.js'
import { shouldShowOpus1mMergeNotice } from './Opus1mMergeNotice.js'

export function VoiceModeNotice(): React.ReactNode {
  // Positive ternary pattern — see docs/feature-gating.md.
  // All strings must be inside the guarded branch for dead-code elimination.
  return feature('VOICE_MODE') ? <VoiceModeNoticeInner /> : null
}

function VoiceModeNoticeInner(): React.ReactNode {
  // Capture eligibility once at mount — no reactive subscriptions. This sits
  // at the top of the message list and enters scrollback quickly; any
  // re-render after it's in scrollback would force a full terminal reset.
  // If the user runs /voice this session, the notice stays visible; it won't
  // show next session since voiceEnabled will be true on disk.
  //
  // The show-once latch lives in the jarvis-owned settings.json (userSettings)
  // — NOT the shared ~/.claude.json global config. Real Claude Code sessions
  // rewrite that file from their own in-memory state and clobber fork-only
  // keys, which made the old voiceNoticeSeenCount counter reset forever (the
  // notice nagged at every session start — user report 2026-07-09).
  const [show] = useState(
    () =>
      isVoiceModeEnabled() &&
      getInitialSettings().voiceEnabled !== true &&
      getInitialSettings().voiceNoticeSeen !== true &&
      !shouldShowOpus1mMergeNotice(),
  )

  useEffect(() => {
    if (!show) return
    updateSettingsForSource('userSettings', { voiceNoticeSeen: true })
  }, [show])

  if (!show) return null

  return (
    <Box paddingLeft={2}>
      <AnimatedAsterisk />
      <Text dimColor> Voice mode is now available · /voice to enable</Text>
    </Box>
  )
}
