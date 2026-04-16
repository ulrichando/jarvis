/**
 * SDK Utility Types - TypeScript types that cannot be expressed as Zod schemas.
 *
 * These types are hand-authored and re-exported from coreTypes.ts.
 */

import type {
  BetaUsage as Usage,
} from '@anthropic-ai/sdk/resources/beta/messages/messages.mjs'

/**
 * Usage with all nullable fields made non-nullable (filled with zeros/defaults).
 * This is the type used throughout the codebase for tracking token usage.
 */
export type NonNullableUsage = {
  [K in keyof Usage]-?: NonNullable<Usage[K]>
} & {
  iterations?: Array<{
    input_tokens: number
    output_tokens: number
    cache_creation_input_tokens: number
    cache_read_input_tokens: number
  }>
  inference_geo?: string
  speed?: 'standard' | 'turbo'
}
