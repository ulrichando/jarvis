// src/cli/src/gh-agent/config.ts
import { readFileSync } from 'node:fs'
import { homedir } from 'node:os'
import { join } from 'node:path'

export type GhAgentConfig = {
  repos: string[]
  allowlist: string[]
  trigger: string
  pollSeconds: number
  maxTasksPerHour: number
  model?: string
}

export const GH_AGENT_DIR = join(homedir(), '.jarvis', 'gh-agent')
export const CONFIG_PATH = join(homedir(), '.jarvis', 'gh-agent.json')

export const DEFAULTS: GhAgentConfig = {
  repos: [],
  allowlist: ['ulrichando'],
  trigger: '@jarvis',
  pollSeconds: 45,
  maxTasksPerHour: 6,
}

export function loadGhAgentConfig(path: string = CONFIG_PATH): GhAgentConfig {
  try {
    const raw = JSON.parse(readFileSync(path, 'utf8')) as Partial<GhAgentConfig>
    return {
      repos: Array.isArray(raw.repos) ? raw.repos : DEFAULTS.repos,
      allowlist: Array.isArray(raw.allowlist) ? raw.allowlist : DEFAULTS.allowlist,
      trigger: typeof raw.trigger === 'string' ? raw.trigger : DEFAULTS.trigger,
      pollSeconds: typeof raw.pollSeconds === 'number' ? raw.pollSeconds : DEFAULTS.pollSeconds,
      maxTasksPerHour: typeof raw.maxTasksPerHour === 'number' ? raw.maxTasksPerHour : DEFAULTS.maxTasksPerHour,
      model: typeof raw.model === 'string' ? raw.model : undefined,
    }
  } catch {
    return { ...DEFAULTS }
  }
}

export function isAllowedAuthor(cfg: GhAgentConfig, login: string): boolean {
  const l = login.toLowerCase()
  return cfg.allowlist.some(a => a.toLowerCase() === l)
}
