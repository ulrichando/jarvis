import * as fs from 'node:fs/promises'
import * as os from 'node:os'
import * as path from 'node:path'

import { z } from 'zod/v4'

import { buildTool, type ToolDef } from '../../Tool.js'
import { lazySchema } from '../../utils/lazySchema.js'

import { SET_LOCATION_TOOL_NAME } from './constants.js'
import { DESCRIPTION, PROMPT } from './prompt.js'

const OVERRIDE_PATH = path.join(os.homedir(), '.jarvis', 'location-override')

const inputSchema = lazySchema(() =>
  z.strictObject({
    city: z
      .string()
      .describe(
        'Free-form location string (e.g. "Cleveland, Ohio, US"). Empty string clears the override.',
      ),
  }),
)
type InputSchema = ReturnType<typeof inputSchema>

const outputSchema = lazySchema(() =>
  z.object({
    status: z.enum(['saved', 'cleared', 'error']),
    location: z.string().nullable(),
    message: z.string(),
  }),
)
type OutputSchema = ReturnType<typeof outputSchema>

export type Output = z.infer<OutputSchema>

export const SetLocationTool = buildTool({
  name: SET_LOCATION_TOOL_NAME,
  searchHint: "pin user's location for weather / regional queries (override IP geo)",
  maxResultSizeChars: 10_000,
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
    return 'SetLocation'
  },
  shouldDefer: true,
  isEnabled() {
    return true
  },
  isConcurrencySafe() {
    return true
  },
  toAutoClassifierInput({ city }) {
    return city
  },
  renderToolUseMessage() {
    return null
  },
  async call({ city }) {
    const trimmed = (city ?? '').trim()
    try {
      await fs.mkdir(path.dirname(OVERRIDE_PATH), { recursive: true })
      if (!trimmed) {
        // Clear the override
        try {
          await fs.unlink(OVERRIDE_PATH)
        } catch (err) {
          // Already absent — treat as success
          if ((err as NodeJS.ErrnoException).code !== 'ENOENT') {
            throw err
          }
        }
        return {
          data: {
            status: 'cleared' as const,
            location: null,
            message: "Location override cleared. I'll use auto-detection.",
          },
        }
      }
      await fs.writeFile(OVERRIDE_PATH, trimmed + '\n', 'utf-8')
      return {
        data: {
          status: 'saved' as const,
          location: trimmed,
          message: `Got it — using ${trimmed} as your location from now on.`,
        },
      }
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err)
      return {
        data: {
          status: 'error' as const,
          location: null,
          message: `Couldn't save location: ${msg}`,
        },
      }
    }
  },
  mapToolResultToToolResultBlockParam(content, toolUseID) {
    const out = content as Output
    return {
      tool_use_id: toolUseID,
      type: 'tool_result',
      content: out.message,
    }
  },
} satisfies ToolDef<InputSchema, Output>)
