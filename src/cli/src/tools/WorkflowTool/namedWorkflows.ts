import { readdir, readFile, stat } from 'node:fs/promises'
import { join } from 'node:path'
import { homedir } from 'node:os'
import { parseWorkflowMeta, type WorkflowMeta } from './meta.js'

const MAX_WORKFLOW_BYTES = 200_000

export type NamedWorkflow = {
  source: 'userSettings' | 'projectSettings'
  name: string
  description: string
  whenToUse?: string
  phases?: WorkflowMeta['phases']
  script: string
  filePath: string
}

let cache: Map<string, NamedWorkflow[]> | null = null

export function clearNamedWorkflowCache(): void {
  cache = null
}

export async function loadWorkflowsFromDir(
  dir: string,
  source: NamedWorkflow['source'],
): Promise<NamedWorkflow[]> {
  let names: string[]
  try {
    names = await readdir(dir)
  } catch {
    return []
  }
  const out: NamedWorkflow[] = []
  for (const file of names) {
    if (!file.endsWith('.mjs') && !file.endsWith('.js')) continue
    const path = join(dir, file)
    try {
      const s = await stat(path)
      if (s.size > MAX_WORKFLOW_BYTES) continue
      const script = await readFile(path, 'utf-8')
      const parsed = parseWorkflowMeta(script)
      if ('error' in parsed) continue
      out.push({
        source,
        name: parsed.meta.name,
        description: parsed.meta.description,
        whenToUse: parsed.meta.whenToUse,
        phases: parsed.meta.phases,
        script,
        filePath: path,
      })
    } catch {
      continue
    }
  }
  return out
}

// User dir + project dir; project wins on name collision. Memoized by cwd.
export async function getAllWorkflows(cwd: string): Promise<NamedWorkflow[]> {
  cache ??= new Map()
  const cached = cache.get(cwd)
  if (cached) return cached
  const [user, project] = await Promise.all([
    loadWorkflowsFromDir(join(homedir(), '.claude', 'workflows'), 'userSettings'),
    loadWorkflowsFromDir(join(cwd, '.claude', 'workflows'), 'projectSettings'),
  ])
  const byName = new Map<string, NamedWorkflow>()
  for (const w of user) byName.set(w.name, w)
  for (const w of project) byName.set(w.name, w)
  const list = [...byName.values()].sort((a, b) => a.name.localeCompare(b.name))
  cache.set(cwd, list)
  return list
}

export async function resolveWorkflowByName(
  name: string,
  cwd: string,
): Promise<NamedWorkflow | undefined> {
  return (await getAllWorkflows(cwd)).find(w => w.name === name)
}
