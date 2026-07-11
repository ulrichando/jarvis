import { afterEach, beforeEach, describe, expect, test } from 'bun:test'
import {
  LOCAL_LEAN_TOOL_NAMES,
  filterToolsForLeanLocalModel,
  isLeanLocalModel,
} from './localModelLean.js'

const OLLAMA_MODEL = 'ollama:qwen3:4b-instruct-2507-q4_K_M'

const FULL_TOOLSET = [
  { name: 'Task' },
  { name: 'Bash' },
  { name: 'Glob' },
  { name: 'Grep' },
  { name: 'Read' },
  { name: 'Edit' },
  { name: 'Write' },
  { name: 'NotebookEdit' },
  { name: 'WebFetch' },
  { name: 'TodoWrite' },
  { name: 'WebSearch' },
  { name: 'Skill' },
  { name: 'AskUserQuestion' },
  { name: 'mcp__blender__get_scene_info' },
]

let savedRegistry: string | undefined
let savedLean: string | undefined

beforeEach(() => {
  savedRegistry = process.env.JARVIS_MODEL_REGISTRY_ENABLED
  savedLean = process.env.JARVIS_OLLAMA_LEAN
  process.env.JARVIS_MODEL_REGISTRY_ENABLED = '1'
  delete process.env.JARVIS_OLLAMA_LEAN
})

afterEach(() => {
  if (savedRegistry === undefined) {
    delete process.env.JARVIS_MODEL_REGISTRY_ENABLED
  } else {
    process.env.JARVIS_MODEL_REGISTRY_ENABLED = savedRegistry
  }
  if (savedLean === undefined) {
    delete process.env.JARVIS_OLLAMA_LEAN
  } else {
    process.env.JARVIS_OLLAMA_LEAN = savedLean
  }
})

describe('isLeanLocalModel', () => {
  test('true for a discovered/synthesized ollama model id', () => {
    expect(isLeanLocalModel(OLLAMA_MODEL)).toBe(true)
  })

  test('false for cloud models, unknown strings, and empty', () => {
    expect(isLeanLocalModel('deepseek-v4-flash')).toBe(false)
    expect(isLeanLocalModel('claude-sonnet-4-6')).toBe(false)
    expect(isLeanLocalModel('totally-unknown-model')).toBe(false)
    expect(isLeanLocalModel('')).toBe(false)
    expect(isLeanLocalModel(null)).toBe(false)
    expect(isLeanLocalModel(undefined)).toBe(false)
  })

  test('false when the registry is disabled', () => {
    delete process.env.JARVIS_MODEL_REGISTRY_ENABLED
    expect(isLeanLocalModel(OLLAMA_MODEL)).toBe(false)
  })

  test('kill-switch JARVIS_OLLAMA_LEAN=0 disables it', () => {
    expect(isLeanLocalModel(OLLAMA_MODEL, { JARVIS_OLLAMA_LEAN: '0' } as any)).toBe(
      false,
    )
    expect(
      isLeanLocalModel(OLLAMA_MODEL, { JARVIS_OLLAMA_LEAN: 'off' } as any),
    ).toBe(false)
  })
})

describe('filterToolsForLeanLocalModel', () => {
  test('keeps only the lean core for a local model (MCP tools dropped)', () => {
    const out = filterToolsForLeanLocalModel(FULL_TOOLSET, OLLAMA_MODEL)
    expect(out.map(t => t.name).sort()).toEqual(
      [...LOCAL_LEAN_TOOL_NAMES].sort(),
    )
  })

  test('cloud models pass through byte-identical', () => {
    const out = filterToolsForLeanLocalModel(FULL_TOOLSET, 'deepseek-v4-flash')
    expect(out).toBe(FULL_TOOLSET)
  })

  test('kill-switch passes through untouched', () => {
    const out = filterToolsForLeanLocalModel(FULL_TOOLSET, OLLAMA_MODEL, {
      JARVIS_OLLAMA_LEAN: '0',
    } as any)
    expect(out).toBe(FULL_TOOLSET)
  })

  test('never returns an empty toolset (disjoint input passes through)', () => {
    const disjoint = [{ name: 'Task' }, { name: 'WebSearch' }]
    const out = filterToolsForLeanLocalModel(disjoint, OLLAMA_MODEL)
    expect(out).toBe(disjoint)
  })

  test('empty input stays empty without a registry lookup blowup', () => {
    expect(filterToolsForLeanLocalModel([], OLLAMA_MODEL)).toEqual([])
  })
})
