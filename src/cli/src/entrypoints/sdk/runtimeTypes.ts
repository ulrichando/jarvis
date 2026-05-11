/**
 * SDK Runtime Types - Non-serializable types for the SDK API.
 *
 * These types include callbacks, interfaces with methods, and other
 * types that cannot be expressed in Zod schemas or JSON.
 */

import type { z } from 'zod/v4'
import type {
  SDKMessage,
  SDKResultMessage,
  SDKSessionInfo,
  SDKUserMessage,
  McpServerConfigForProcessTransport,
  PermissionMode,
  PermissionResult,
  HookJSONOutput,
  HookInput,
  SdkPluginConfig,
  ThinkingConfig,
} from './coreTypes.js'

// ============================================================================
// Zod schema utility types
// ============================================================================

export type AnyZodRawShape = z.ZodRawShape

export type InferShape<T extends AnyZodRawShape> = {
  [K in keyof T]: z.infer<T[K]>
}

// ============================================================================
// Effort Types
// ============================================================================

export type EffortLevel = 'low' | 'medium' | 'high' | 'xhigh' | 'max'

// ============================================================================
// MCP Tool Definition
// ============================================================================

export type SdkMcpToolDefinition<Schema extends AnyZodRawShape = AnyZodRawShape> = {
  name: string
  description: string
  inputSchema: Schema
  handler: (args: InferShape<Schema>, extra: unknown) => Promise<unknown>
  annotations?: {
    readOnly?: boolean
    destructive?: boolean
    openWorld?: boolean
  }
  searchHint?: string
  alwaysLoad?: boolean
}

export type McpSdkServerConfigWithInstance = McpServerConfigForProcessTransport & {
  type: 'sdk'
  name: string
  instance?: unknown
}

// ============================================================================
// Hook Callback Types
// ============================================================================

export type HookCallback = (
  input: HookInput,
) => Promise<HookJSONOutput | void> | HookJSONOutput | void

export type PermissionCallback = (
  toolName: string,
  toolInput: Record<string, unknown>,
) => Promise<PermissionResult> | PermissionResult

// ============================================================================
// Query Options
// ============================================================================

export type Options = {
  /** Directory to run the session in. Defaults to process.cwd(). */
  cwd?: string
  /** Abort signal to cancel the session. */
  abortSignal?: AbortSignal
  /** System prompt to prepend to all conversations. */
  systemPrompt?: string
  /** Append to the system prompt. */
  appendSystemPrompt?: string
  /** Permission mode for tool execution. */
  permissionMode?: PermissionMode
  /** Path to config file. */
  configFilePath?: string
  /** Model to use for the session. */
  model?: string
  /** Callback for permission decisions. */
  onPermissionRequest?: PermissionCallback
  /** MCP servers to enable for this session. */
  mcpServers?: Record<string, McpServerConfigForProcessTransport>
  /** Custom SDK MCP servers running in-process. */
  sdkMcpServers?: McpSdkServerConfigWithInstance[]
  /** Maximum number of turns before stopping. */
  maxTurns?: number
  /** Whether to allow dangerous bypass of permissions. */
  allowDangerouslySkipPermissions?: boolean
  /** Thinking configuration. */
  thinkingConfig?: ThinkingConfig
  /** Custom tools for the session. */
  tools?: SdkMcpToolDefinition[]
  /** Plugins to load. */
  plugins?: SdkPluginConfig[]
  /** Output format configuration. */
  outputFormat?: {
    type: 'json_schema'
    schema: Record<string, unknown>
  }
}

/** @internal */
export type InternalOptions = Options & {
  _internal?: Record<string, unknown>
}

// ============================================================================
// Query Types
// ============================================================================

export type Query = AsyncIterable<SDKMessage> & {
  /** Abort the query. */
  abort(): void
  /** Get the final result. */
  result(): Promise<SDKResultMessage>
}

/** @internal */
export type InternalQuery = Query & {
  _internal?: unknown
}

// ============================================================================
// Session Types (V2 API - UNSTABLE)
// ============================================================================

export type SDKSessionOptions = {
  /** Directory to run the session in. Defaults to process.cwd(). */
  cwd?: string
  /** Abort signal to cancel the session. */
  abortSignal?: AbortSignal
  /** System prompt to prepend to all conversations. */
  systemPrompt?: string
  /** Permission mode for tool execution. */
  permissionMode?: PermissionMode
  /** Model to use for the session. */
  model?: string
  /** MCP servers to enable for this session. */
  mcpServers?: Record<string, McpServerConfigForProcessTransport>
}

export interface SDKSession {
  /** Session ID. */
  readonly sessionId: string
  /** Send a prompt to the session. */
  prompt(
    message: string | AsyncIterable<SDKUserMessage>,
    options?: Partial<SDKSessionOptions>,
  ): Query
  /** Abort the current turn. */
  abort(): void
  /** Get session info. */
  getInfo(): Promise<SDKSessionInfo | undefined>
}

// ============================================================================
// Session Management Types
// ============================================================================

export type ListSessionsOptions = {
  dir?: string
  limit?: number
  offset?: number
  includeWorktrees?: boolean
}

export type GetSessionInfoOptions = {
  dir?: string
}

export type GetSessionMessagesOptions = {
  dir?: string
  limit?: number
  offset?: number
  includeSystemMessages?: boolean
}

export type SessionMutationOptions = {
  dir?: string
}

export type ForkSessionOptions = {
  dir?: string
  upToMessageId?: string
  title?: string
}

export type ForkSessionResult = {
  sessionId: string
}

export type SessionMessage = {
  role: 'user' | 'assistant'
  content: unknown
  uuid?: string
  parentUuid?: string
}
