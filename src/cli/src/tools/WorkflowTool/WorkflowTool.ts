import { randomUUID } from 'node:crypto'
import { mkdir, readFile, writeFile } from 'node:fs/promises'
import { homedir } from 'node:os'
import { join } from 'node:path'
import vm from 'node:vm'
import { z } from 'zod/v4'
import { buildTool, type ToolDef } from '../../Tool.js'
import { generateTaskId, createTaskStateBase } from '../../Task.js'
import { registerTask, updateTaskState } from '../../utils/task/framework.js'
import { lazySchema } from '../../utils/lazySchema.js'
import { getCwd } from '../../utils/cwd.js'
import {
  getSessionProjectDir,
  getOriginalCwd,
  getSessionId,
} from '../../bootstrap/state.js'
import { getProjectDir } from '../../utils/sessionStorage.js'
import type { LocalWorkflowTaskState } from '../../tasks/LocalWorkflowTask/LocalWorkflowTask.js'
import type { SdkWorkflowProgress } from '../../types/tools.js'
import type { PermissionResult } from '../../types/permissions.js'
import { parseWorkflowMeta, checkDeterminism } from './meta.js'
import { runWorkflow } from './runWorkflow.js'
import { makeDispatch } from './dispatch.js'
import { resolveWorkflowByName } from './namedWorkflows.js'
import { WORKFLOW_TOOL_PROMPT } from './prompt.js'

export const WORKFLOW_TOOL_NAME = 'Workflow'

// ── validation helper (tested directly by WorkflowTool.test.ts) ──────────────

export function validateWorkflowScript(
  script: string,
): { ok: true } | { ok: false; error: string } {
  const parsed = parseWorkflowMeta(script)
  if ('error' in parsed) return { ok: false, error: parsed.error }
  if (!checkDeterminism(parsed.scriptBody)) {
    return {
      ok: false,
      error:
        'Workflow scripts must be deterministic: Date.now()/Math.random()/new Date() are unavailable (breaks resume). Stamp results after the workflow returns, or pass timestamps via args.',
    }
  }
  return { ok: true }
}

// ── schema ────────────────────────────────────────────────────────────────────

const inputSchema = lazySchema(() =>
  z
    .strictObject({
      script: z
        .string()
        .max(100_000)
        .optional()
        .describe(
          'Self-contained workflow script. Must begin with `export const meta = { name, description, phases }` (pure literal) then the body using agent()/parallel()/pipeline()/phase().',
        ),
      name: z
        .string()
        .optional()
        .describe('Name of a predefined workflow from .claude/workflows/.'),
      description: z
        .string()
        .optional()
        .describe('Ignored — set in the meta block.'),
      title: z.string().optional().describe('Ignored — set in the meta block.'),
      args: z
        .unknown()
        .optional()
        .describe('Optional JSON exposed to the script as global `args`.'),
      scriptPath: z
        .string()
        .optional()
        .describe(
          'Path to a workflow script on disk. Takes precedence over script/name.',
        ),
      resumeFromRunId: z
        .string()
        .regex(/^wf_[a-z0-9-]{6,}$/)
        .optional()
        .describe(
          'Run ID to resume from; completed agent() calls return cached results.',
        ),
    })
    .refine(v => Boolean(v.script || v.name || v.scriptPath), {
      message: 'Must provide script, name, or scriptPath',
    }),
)
type InputSchema = ReturnType<typeof inputSchema>

const outputSchema = lazySchema(() =>
  z.object({
    status: z.string(),
    taskId: z.string(),
    workflowName: z.string().optional(),
    runId: z.string().optional(),
    summary: z.string().optional(),
    transcriptDir: z.string().optional(),
    scriptPath: z.string().optional(),
    error: z.string().optional(),
  }),
)
type OutputSchema = ReturnType<typeof outputSchema>
type Output = z.infer<OutputSchema>

// ── session-dir helper ────────────────────────────────────────────────────────

function getWorkflowSessionDir(runId: string): string {
  try {
    const projectDir = getSessionProjectDir() ?? getProjectDir(getOriginalCwd())
    const sessionId = getSessionId()
    return join(projectDir, String(sessionId), 'subagents', 'workflows', runId)
  } catch {
    // Fallback when bootstrap state is not available (tests, SDK-headless runs)
    return join(homedir(), '.jarvis', 'workflows', runId)
  }
}

// ── tool ─────────────────────────────────────────────────────────────────────

export const WorkflowTool = buildTool({
  name: WORKFLOW_TOOL_NAME,
  searchHint: 'orchestrate multi-agent workflows',
  maxResultSizeChars: 20_000,
  get inputSchema(): InputSchema {
    return inputSchema()
  },
  get outputSchema(): OutputSchema {
    return outputSchema()
  },
  isEnabled() {
    return true
  },
  isReadOnly() {
    return false
  },
  isConcurrencySafe() {
    return true
  },
  async description() {
    return WORKFLOW_TOOL_PROMPT
  },
  async prompt() {
    return WORKFLOW_TOOL_PROMPT
  },
  renderToolUseMessage() {
    return null
  },

  async checkPermissions(
    input,
    _context,
  ): Promise<PermissionResult> {
    // Resolve the script source so the permission dialog shows what will run
    let resolvedScript: string | undefined
    try {
      if (input.scriptPath) {
        resolvedScript = await readFile(input.scriptPath, 'utf-8')
      } else if (input.name) {
        const wf = await resolveWorkflowByName(input.name, getCwd())
        resolvedScript = wf?.script
      } else {
        resolvedScript = input.script
      }
    } catch {
      resolvedScript = input.script
    }

    const updatedInput = resolvedScript
      ? { ...input, script: resolvedScript }
      : input

    return {
      behavior: 'ask',
      message: input.name
        ? `Run workflow: ${input.name}`
        : 'Review dynamic workflow before running',
      updatedInput,
    }
  },

  async call(input, context) {
    // 1. Resolve the script text
    let script: string | undefined
    let resolvedScriptPath: string | undefined

    if (input.scriptPath) {
      try {
        script = await readFile(input.scriptPath, 'utf-8')
        resolvedScriptPath = input.scriptPath
      } catch (e) {
        throw new Error(
          `Cannot read workflow scriptPath "${input.scriptPath}": ${(e as Error).message}`,
        )
      }
    } else if (input.name) {
      const wf = await resolveWorkflowByName(input.name, getCwd())
      if (!wf) {
        throw new Error(`No workflow named "${input.name}" found in .claude/workflows/`)
      }
      script = wf.script
      resolvedScriptPath = wf.filePath
    } else {
      script = input.script
    }

    if (!script) {
      throw new Error('Workflow requires script, name, or scriptPath')
    }

    // 2. Parse meta
    const parsed = parseWorkflowMeta(script)
    if ('error' in parsed) {
      throw new Error(`Workflow script meta error: ${parsed.error}`)
    }

    // 3. Syntax precheck
    try {
      new vm.Script(`(async()=>{${parsed.scriptBody}})()`)
    } catch (e) {
      return {
        data: {
          status: 'async_launched',
          taskId: generateTaskId('local_workflow'),
          workflowName: parsed.meta.name,
          error: 'Workflow script has a syntax error: ' + (e as Error).message,
        },
      }
    }

    // 4. IDs
    const runId = input.resumeFromRunId ?? ('wf_' + randomUUID().slice(0, 12))
    const taskId = generateTaskId('local_workflow')

    // 5. Build and register task state
    const taskState: LocalWorkflowTaskState = {
      ...createTaskStateBase(
        taskId,
        'local_workflow',
        parsed.meta.name,
        context.toolUseId,
      ),
      type: 'local_workflow',
      status: 'running',
      workflowName: parsed.meta.name,
      workflowRunId: runId,
      summary: parsed.meta.description,
      title: parsed.meta.name,
      phases: parsed.meta.phases,
      agentCount: 0,
      workflowProgress: [],
      totalTokens: 0,
      totalToolCalls: 0,
      agentControllers: new Map(),
      runController: new AbortController(),
    }

    const setAppStateForTasks =
      context.setAppStateForTasks ?? context.setAppState
    registerTask(taskState, setAppStateForTasks)

    // 6. Persist the script (best-effort)
    const sessionDir = getWorkflowSessionDir(runId)
    const persistedScriptPath = join(sessionDir, 'script.mjs')
    const transcriptDir = sessionDir

    try {
      await mkdir(sessionDir, { recursive: true })
      await writeFile(persistedScriptPath, script, { encoding: 'utf-8' })
    } catch {
      // Persistence failure must NOT block launch
    }

    // 7. Fire background runner (do NOT await)
    void (async () => {
      const runController = (taskState as LocalWorkflowTaskState).runController!

      const onProgress = (p: SdkWorkflowProgress) => {
        updateTaskState<LocalWorkflowTaskState>(
          taskId,
          setAppStateForTasks,
          task => ({
            ...task,
            workflowProgress: [...(task.workflowProgress ?? []), p],
            totalTokens:
              (task.totalTokens ?? 0) +
              (p.type === 'workflow_agent' ? (p.tokens ?? 0) : 0),
            totalToolCalls:
              (task.totalToolCalls ?? 0) +
              (p.type === 'workflow_agent' ? (p.toolCalls ?? 0) : 0),
          }),
        )
      }

      const dispatch = makeDispatch({
        toolUseContext: context,
        defaultModel: context.options.mainLoopModel,
        runId,
        agentControllers: (taskState as LocalWorkflowTaskState).agentControllers,
        resolveAgentType: () => undefined,
      })

      const out = await runWorkflow({
        scriptBody: parsed.scriptBody,
        args: input.args,
        dispatch,
        getBudget: () => ({
          total: null,
          spent: () => 0,
          remaining: () => Infinity,
        }),
        resolveWorkflow: async () => null,
        onProgress,
        signal: runController.signal,
        syncTimeoutMs: 30_000,
      })

      const status = runController.signal.aborted
        ? 'killed'
        : out.error
          ? 'failed'
          : 'completed'

      updateTaskState<LocalWorkflowTaskState>(
        taskId,
        setAppStateForTasks,
        task => ({
          ...task,
          status,
          endTime: Date.now(),
          notified: false,
          summary: out.error
            ? `${task.summary ?? ''} (error: ${out.error.split('\n')[0]})`
            : task.summary,
        }),
      )

      // Persist journal (best-effort)
      try {
        const journalPath = join(sessionDir, 'journal.jsonl')
        const lines = out.journal.map(e => JSON.stringify(e)).join('\n')
        if (lines) await writeFile(journalPath, lines + '\n', { encoding: 'utf-8' })
      } catch {
        // Best-effort only
      }
    })().catch(() => {
      // Mark failed best-effort
      updateTaskState<LocalWorkflowTaskState>(
        taskId,
        setAppStateForTasks,
        task =>
          task.status === 'running'
            ? { ...task, status: 'failed', endTime: Date.now(), notified: false }
            : task,
      )
    })

    // 8. Return immediately
    return {
      data: {
        status: 'async_launched',
        taskId,
        workflowName: parsed.meta.name,
        runId,
        summary: parsed.meta.description,
        transcriptDir,
        scriptPath: resolvedScriptPath ?? persistedScriptPath,
      },
    }
  },

  mapToolResultToToolResultBlockParam(output, toolUseID) {
    if (output.error) {
      return {
        tool_use_id: toolUseID,
        type: 'tool_result',
        content:
          'Workflow script has a syntax error and was not launched:\n' +
          output.error,
        is_error: true,
      }
    }
    const content =
      `Workflow launched in background. Task ID: ${output.taskId}` +
      (output.summary ? `\nSummary: ${output.summary}` : '') +
      (output.transcriptDir ? `\nTranscript dir: ${output.transcriptDir}` : '') +
      (output.scriptPath ? `\nScript file: ${output.scriptPath}` : '') +
      (output.runId ? `\nRun ID: ${output.runId}` : '') +
      '\n\nYou will be notified when it completes. Use /workflows to watch live progress.'
    return {
      tool_use_id: toolUseID,
      type: 'tool_result',
      content,
      is_error: false,
    }
  },
} satisfies ToolDef<InputSchema, Output>)
