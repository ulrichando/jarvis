import type { Command } from '../../commands.js'
import {
  isVoiceGrowthBookEnabled,
  isVoiceModeEnabled,
} from '../../voice/voiceModeEnabled.js'

const voice = {
  type: 'local',
  name: 'voice',
  description: 'Toggle voice mode',
  isEnabled: () => isVoiceGrowthBookEnabled(),
  get isHidden() {
    return !isVoiceModeEnabled()
  },
  supportsNonInteractive: false,
  load: () => import('./voice.js'),
} satisfies Command

export default voice

export const voiceRestart: Command = {
  type: 'local',
  name: 'voice-restart',
  description: 'Safely restart the jarvis-voice-agent service',
  isEnabled: () => isVoiceGrowthBookEnabled(),
  get isHidden() {
    return !isVoiceModeEnabled()
  },
  supportsNonInteractive: true,
  load: () => import('./restart.js'),
}

export const voiceLogs: Command = {
  type: 'local',
  name: 'voice-logs',
  description: 'Show recent voice-agent ERROR/WARNING log lines',
  isEnabled: () => isVoiceGrowthBookEnabled(),
  get isHidden() {
    return !isVoiceModeEnabled()
  },
  supportsNonInteractive: true,
  load: () => import('./logs.js'),
}
