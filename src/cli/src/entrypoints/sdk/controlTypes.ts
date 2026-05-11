/**
 * SDK Control Types - Types for the control protocol between SDK implementations and the CLI.
 */

import type {
  AgentDefinition,
  AgentInfo,
  AccountInfo,
  FastModeState,
  HookEvent,
  HookInput,
  McpServerConfigForProcessTransport,
  McpServerStatus,
  McpSetServersResult,
  ModelInfo,
  PermissionMode,
  PermissionUpdate,
  SDKMessage,
  SDKPostTurnSummaryMessage,
  SDKStreamlinedTextMessage,
  SDKStreamlinedToolUseSummaryMessage,
  SDKUserMessage,
  SlashCommand,
} from './coreTypes.js'

// ============================================================================
// Hook Callback Types
// ============================================================================

export type SDKHookCallbackMatcher = {
  matcher?: string
  hookCallbackIds: string[]
  timeout?: number
}

// ============================================================================
// Control Request Types
// ============================================================================

export type SDKControlInitializeRequest = {
  subtype: 'initialize'
  hooks?: Record<HookEvent, SDKHookCallbackMatcher[]>
  sdkMcpServers?: string[]
  jsonSchema?: Record<string, unknown>
  systemPrompt?: string
  appendSystemPrompt?: string
  agents?: Record<string, AgentDefinition>
  promptSuggestions?: boolean
  agentProgressSummaries?: boolean
}

export type SDKControlInitializeResponse = {
  commands: SlashCommand[]
  agents: AgentInfo[]
  output_style: string
  available_output_styles: string[]
  models: ModelInfo[]
  account: AccountInfo
  pid?: number
  fast_mode_state?: FastModeState
}

export type SDKControlInterruptRequest = {
  subtype: 'interrupt'
}

export type SDKControlPermissionRequest = {
  subtype: 'can_use_tool'
  tool_name: string
  input: Record<string, unknown>
  permission_suggestions?: PermissionUpdate[]
  blocked_path?: string
  decision_reason?: string
  title?: string
  display_name?: string
  tool_use_id: string
  agent_id?: string
  description?: string
}

export type SDKControlSetPermissionModeRequest = {
  subtype: 'set_permission_mode'
  mode: PermissionMode
  ultraplan?: boolean
}

export type SDKControlSetModelRequest = {
  subtype: 'set_model'
  model?: string
}

export type SDKControlSetMaxThinkingTokensRequest = {
  subtype: 'set_max_thinking_tokens'
  max_thinking_tokens: number | null
}

export type SDKControlMcpStatusRequest = {
  subtype: 'mcp_status'
}

export type SDKControlMcpStatusResponse = {
  mcpServers: McpServerStatus[]
}

export type SDKControlGetContextUsageRequest = {
  subtype: 'get_context_usage'
}

export type SDKControlGetContextUsageResponse = {
  categories: Array<{
    name: string
    tokens: number
    color: string
    isDeferred?: boolean
  }>
  totalTokens: number
  maxTokens: number
  rawMaxTokens: number
  percentage: number
  gridRows: Array<Array<{
    color: string
    isFilled: boolean
    categoryName: string
    tokens: number
    percentage: number
    squareFullness: number
  }>>
  model: string
  memoryFiles: Array<{ path: string; type: string; tokens: number }>
  mcpTools: Array<{ name: string; serverName: string; tokens: number; isLoaded?: boolean }>
  deferredBuiltinTools?: Array<{ name: string; tokens: number; isLoaded: boolean }>
  systemTools?: Array<{ name: string; tokens: number }>
  systemPromptSections?: Array<{ name: string; tokens: number }>
  agents: Array<{ agentType: string; source: string; tokens: number }>
  slashCommands?: { totalCommands: number; includedCommands: number; tokens: number }
  skills?: {
    totalSkills: number
    includedSkills: number
    tokens: number
    skillFrontmatter: Array<{ name: string; source: string; tokens: number }>
  }
  autoCompactThreshold?: number
  isAutoCompactEnabled: boolean
  messageBreakdown?: {
    toolCallTokens: number
    toolResultTokens: number
    attachmentTokens: number
    assistantMessageTokens: number
    userMessageTokens: number
    toolCallsByType: Array<{ name: string; callTokens: number; resultTokens: number }>
    attachmentsByType: Array<{ name: string; tokens: number }>
  }
  apiUsage: {
    input_tokens: number
    output_tokens: number
    cache_creation_input_tokens: number
    cache_read_input_tokens: number
  } | null
}

export type SDKControlRewindFilesRequest = {
  subtype: 'rewind_files'
  user_message_id: string
  dry_run?: boolean
}

export type SDKControlRewindFilesResponse = {
  canRewind: boolean
  error?: string
  filesChanged?: string[]
  insertions?: number
  deletions?: number
}

export type SDKControlCancelAsyncMessageRequest = {
  subtype: 'cancel_async_message'
  message_uuid: string
}

export type SDKControlCancelAsyncMessageResponse = {
  cancelled: boolean
}

export type SDKControlSeedReadStateRequest = {
  subtype: 'seed_read_state'
  path: string
  mtime: number
}

export type SDKHookCallbackRequest = {
  subtype: 'hook_callback'
  callback_id: string
  input: HookInput
  tool_use_id?: string
}

export type SDKControlMcpMessageRequest = {
  subtype: 'mcp_message'
  server_name: string
  message: unknown
}

export type SDKControlMcpSetServersRequest = {
  subtype: 'mcp_set_servers'
  servers: Record<string, McpServerConfigForProcessTransport>
}

export type SDKControlMcpSetServersResponse = McpSetServersResult

export type SDKControlReloadPluginsRequest = {
  subtype: 'reload_plugins'
}

export type SDKControlReloadPluginsResponse = {
  commands: SlashCommand[]
  agents: AgentInfo[]
  plugins: Array<{ name: string; path: string; source?: string }>
  mcpServers: McpServerStatus[]
  error_count: number
}

export type SDKControlMcpReconnectRequest = {
  subtype: 'mcp_reconnect'
  serverName: string
}

export type SDKControlMcpToggleRequest = {
  subtype: 'mcp_toggle'
  serverName: string
  enabled: boolean
}

export type SDKControlStopTaskRequest = {
  subtype: 'stop_task'
  task_id: string
}

export type SDKControlApplyFlagSettingsRequest = {
  subtype: 'apply_flag_settings'
  settings: Record<string, unknown>
}

export type SDKControlGetSettingsRequest = {
  subtype: 'get_settings'
}

export type SDKControlGetSettingsResponse = {
  effective: Record<string, unknown>
  sources: Array<{
    source: 'userSettings' | 'projectSettings' | 'localSettings' | 'flagSettings' | 'policySettings'
    settings: Record<string, unknown>
  }>
  applied?: {
    model: string
    effort: 'low' | 'medium' | 'high' | 'xhigh' | 'max' | null
  }
}

export type SDKControlElicitationRequest = {
  subtype: 'elicitation'
  mcp_server_name: string
  message: string
  mode?: 'form' | 'url'
  url?: string
  elicitation_id?: string
  requested_schema?: Record<string, unknown>
}

export type SDKControlElicitationResponse = {
  action: 'accept' | 'decline' | 'cancel'
  content?: Record<string, unknown>
}

export type SDKControlRequestInner =
  | SDKControlInterruptRequest
  | SDKControlPermissionRequest
  | SDKControlInitializeRequest
  | SDKControlSetPermissionModeRequest
  | SDKControlSetModelRequest
  | SDKControlSetMaxThinkingTokensRequest
  | SDKControlMcpStatusRequest
  | SDKControlGetContextUsageRequest
  | SDKHookCallbackRequest
  | SDKControlMcpMessageRequest
  | SDKControlRewindFilesRequest
  | SDKControlCancelAsyncMessageRequest
  | SDKControlSeedReadStateRequest
  | SDKControlMcpSetServersRequest
  | SDKControlReloadPluginsRequest
  | SDKControlMcpReconnectRequest
  | SDKControlMcpToggleRequest
  | SDKControlStopTaskRequest
  | SDKControlApplyFlagSettingsRequest
  | SDKControlGetSettingsRequest
  | SDKControlElicitationRequest

export type SDKControlRequest = {
  type: 'control_request'
  request_id: string
  request: SDKControlRequestInner
}

export type ControlResponse = {
  subtype: 'success'
  request_id: string
  response?: Record<string, unknown>
}

export type ControlErrorResponse = {
  subtype: 'error'
  request_id: string
  error: string
  pending_permission_requests?: SDKControlRequest[]
}

export type SDKControlResponse = {
  type: 'control_response'
  response: ControlResponse | ControlErrorResponse
}

export type SDKControlCancelRequest = {
  type: 'control_cancel_request'
  request_id: string
}

export type SDKKeepAliveMessage = {
  type: 'keep_alive'
}

export type SDKUpdateEnvironmentVariablesMessage = {
  type: 'update_environment_variables'
  variables: Record<string, string>
}

export type StdoutMessage =
  | SDKMessage
  | SDKStreamlinedTextMessage
  | SDKStreamlinedToolUseSummaryMessage
  | SDKPostTurnSummaryMessage
  | SDKControlResponse
  | SDKControlRequest
  | SDKControlCancelRequest
  | SDKKeepAliveMessage

export type StdinMessage =
  | SDKUserMessage
  | SDKControlRequest
  | SDKControlResponse
  | SDKKeepAliveMessage
  | SDKUpdateEnvironmentVariablesMessage
