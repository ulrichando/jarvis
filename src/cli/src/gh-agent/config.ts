// src/cli/src/gh-agent/config.ts
import { readFileSync } from 'node:fs'
import { homedir } from 'node:os'
import { join } from 'node:path'

export type GhAgentConfig = {
  repos: string[]
  allowlist: string[]
  trigger: string
  pollSeconds: number
  executionTimeoutSec: number
  // P3 adds rate-limiting (maxTasksPerHour) + model override.
}

export const GH_AGENT_DIR = join(homedir(), '.jarvis', 'gh-agent')
export const CONFIG_PATH = join(homedir(), '.jarvis', 'gh-agent.json')

export const DEFAULTS: GhAgentConfig = {
  repos: [],
  allowlist: ['ulrichando'],
  trigger: '@jarvis',
  pollSeconds: 45,
  executionTimeoutSec: 600,
}

export function loadGhAgentConfig(path: string = CONFIG_PATH): GhAgentConfig {
  try {
    const raw = JSON.parse(readFileSync(path, 'utf8')) as Partial<GhAgentConfig>
    // String-only filter: a stray non-string element must not throw mid-sweep
    // later (e.g. .toLowerCase() on a number in isAllowedAuthor).
    const strings = (v: unknown): string[] =>
      Array.isArray(v) ? v.filter((x): x is string => typeof x === 'string') : []
    return {
      repos: Array.isArray(raw.repos) ? strings(raw.repos) : DEFAULTS.repos,
      allowlist: Array.isArray(raw.allowlist) ? strings(raw.allowlist) : DEFAULTS.allowlist,
      trigger: typeof raw.trigger === 'string' ? raw.trigger : DEFAULTS.trigger,
      pollSeconds: typeof raw.pollSeconds === 'number' ? raw.pollSeconds : DEFAULTS.pollSeconds,
      // Clamp: 0/negative/NaN would mean an instant (or no) kill for jarvis -p.
      executionTimeoutSec: (typeof raw.executionTimeoutSec === 'number' && raw.executionTimeoutSec > 0) ? raw.executionTimeoutSec : DEFAULTS.executionTimeoutSec,
    }
  } catch {
    return { ...DEFAULTS }
  }
}

export function isAllowedAuthor(cfg: GhAgentConfig, login: string): boolean {
  const l = login.toLowerCase()
  return cfg.allowlist.some(a => a.toLowerCase() === l)
}
