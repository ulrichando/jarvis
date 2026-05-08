import { spawn, spawnSync } from 'node:child_process'
import { z } from 'zod/v4'

import { buildTool, type ToolDef } from '../../Tool.js'
import { logForDebugging } from '../../utils/debug.js'
import { lazySchema } from '../../utils/lazySchema.js'

import { VOICE_SPEAK_TOOL_NAME } from './constants.js'
import { DESCRIPTION, PROMPT } from './prompt.js'

const MAX_TEXT_LENGTH = 1000

const inputSchema = lazySchema(() =>
  z.strictObject({
    text: z
      .string()
      .min(1)
      .max(MAX_TEXT_LENGTH)
      .describe('The text to speak aloud. Keep it concise — max ~1000 characters.'),
    voice: z
      .enum(['male', 'female'])
      .optional()
      .describe('Voice gender: "male" (default) or "female".'),
    speed: z
      .number()
      .int()
      .min(80)
      .max(450)
      .optional()
      .describe('Speech rate in words-per-minute (80–450, default ~160).'),
  }),
)
type InputSchema = ReturnType<typeof inputSchema>

const outputSchema = lazySchema(() =>
  z.object({
    spoken: z.boolean(),
    chars: z.number(),
    backend: z.string(),
  }),
)
type OutputSchema = ReturnType<typeof outputSchema>
export type Output = z.infer<OutputSchema>

function findPlayCommand(): string | null {
  const candidates = ['pw-play', 'paplay', 'aplay']
  for (const cmd of candidates) {
    const result = spawnSync(cmd, ['--version'], {
      stdio: 'ignore',
      timeout: 3000,
    })
    if (result.error === undefined) return cmd
  }
  return null
}

async function speak(args: {
  text: string
  voice?: 'male' | 'female'
  speed?: number
}): Promise<{ success: boolean; backend: string }> {
  const playCmd = findPlayCommand()
  if (!playCmd) {
    return { success: false, backend: 'none' }
  }

  const espeakArgs: string[] = ['--stdout']

  if (args.voice === 'female') {
    // espeak-ng female voice variants on Linux
    espeakArgs.push('-v', 'en-us+f3')
  } else {
    espeakArgs.push('-v', 'en-us')
  }

  if (args.speed) {
    espeakArgs.push('-s', String(args.speed))
  }

  espeakArgs.push('--', args.text)

  return new Promise(resolve => {
    const espeak = spawn('espeak-ng', espeakArgs, {
      stdio: ['ignore', 'pipe', 'pipe'],
    })

    const play = spawn(playCmd, ['-'], {
      stdio: [espeak.stdout, 'ignore', 'pipe'],
    })

    let stderr = ''
    play.stderr?.on('data', (d: Buffer) => {
      stderr += d.toString()
    })
    espeak.stderr?.on('data', (d: Buffer) => {
      stderr += d.toString()
    })

    play.on('close', code => {
      if (code === 0) {
        resolve({ success: true, backend: `espeak-ng → ${playCmd}` })
      } else {
        logForDebugging(`[VoiceSpeak] play failed (exit ${code}): ${stderr.slice(0, 200)}`)
        resolve({ success: false, backend: `espeak-ng → ${playCmd} (exit ${code})` })
      }
    })

    play.on('error', err => {
      logForDebugging(`[VoiceSpeak] spawn error: ${err.message}`)
      espeak.kill()
      resolve({ success: false, backend: `espeak-ng → ${playCmd}` })
    })

    espeak.on('error', err => {
      logForDebugging(`[VoiceSpeak] espeak error: ${err.message}`)
      resolve({ success: false, backend: 'espeak-ng' })
    })
  })
}

export const VoiceSpeakTool = buildTool({
  name: VOICE_SPEAK_TOOL_NAME,
  searchHint: 'text-to-speech output speak aloud',
  maxResultSizeChars: 500,
  async description() {
    return DESCRIPTION
  },
  async prompt() {
    return PROMPT
  },
  get inputSchema(): InputSchema {
    return inputSchema()
  },
  get outputSchema(): OutputSchema {
    return outputSchema()
  },
  userFacingName() {
    return 'VoiceSpeak'
  },
  shouldDefer: true,
  isEnabled() {
    return true
  },
  isConcurrencySafe() {
    // Only one TTS playback at a time to avoid audio overlap
    return false
  },
  isReadOnly() {
    // Produces audio output; doesn't modify filesystem
    return true
  },
  toAutoClassifierInput({ text }) {
    return text
  },
  renderToolUseMessage() {
    return null
  },
  async call({ text, voice, speed }) {
    const result = await speak({ text, voice, speed })
    return {
      data: {
        spoken: result.success,
        chars: text.length,
        backend: result.backend,
      },
    }
  },
  mapToolResultToToolResultBlockParam(content, toolUseID) {
    const out = content as Output
    if (out.spoken) {
      return {
        tool_use_id: toolUseID,
        type: 'tool_result',
        content: `Spoke ${out.chars} characters via ${out.backend}.`,
      }
    }
    return {
      tool_use_id: toolUseID,
      type: 'tool_result',
      content: `Speech failed — no audio backend available. Ensure espeak-ng and PipeWire/PulseAudio/ALSA are installed.`,
    }
  },
} satisfies ToolDef<InputSchema, Output>)
