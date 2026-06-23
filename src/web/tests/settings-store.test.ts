// @vitest-environment node
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest'
import { promises as fs } from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { DEFAULT_SETTINGS } from '@/lib/settings/schema'

let origCwd: string
const tmps: string[] = []

async function mktmp(): Promise<string> {
  const d = await fs.mkdtemp(path.join(os.tmpdir(), 'jarvis-settings-'))
  tmps.push(d)
  return d
}

async function writeSettings(dir: string, name: string) {
  await fs.mkdir(path.join(dir, '.jarvis'), { recursive: true })
  await fs.writeFile(
    path.join(dir, '.jarvis', 'settings.json'),
    JSON.stringify({ ...DEFAULT_SETTINGS, user: { ...DEFAULT_SETTINGS.user, name } }),
  )
}

// store.ts reads os.homedir()/process.cwd() at import → reset + reimport per case.
async function loadStore(home: string, cwd: string) {
  vi.resetModules()
  vi.stubEnv('HOME', home)
  process.chdir(cwd)
  return await import('@/lib/settings/store')
}

beforeEach(() => {
  origCwd = process.cwd()
})

afterEach(async () => {
  process.chdir(origCwd)
  vi.unstubAllEnvs()
  for (const d of tmps.splice(0)) await fs.rm(d, { recursive: true, force: true })
})

describe('settings store path + migration', () => {
  test('reads from ~/.jarvis (new path)', async () => {
    const home = await mktmp()
    const cwd = await mktmp()
    await writeSettings(home, 'FROM_NEW')
    const { loadSettings } = await loadStore(home, cwd)
    expect((await loadSettings()).user.name).toBe('FROM_NEW')
  })

  test('migrates from legacy cwd/.jarvis when new path absent', async () => {
    const home = await mktmp() // no .jarvis here
    const cwd = await mktmp()
    await writeSettings(cwd, 'FROM_LEGACY')
    const { loadSettings, saveSettings } = await loadStore(home, cwd)
    const loaded = await loadSettings()
    expect(loaded.user.name).toBe('FROM_LEGACY')
    // saving writes the NEW path, completing the migration
    await saveSettings(loaded)
    await expect(
      fs.access(path.join(home, '.jarvis', 'settings.json')),
    ).resolves.toBeUndefined()
  })

  test('defaults when neither location exists', async () => {
    const home = await mktmp()
    const cwd = await mktmp()
    const { loadSettings } = await loadStore(home, cwd)
    expect((await loadSettings()).user.name).toBe(DEFAULT_SETTINGS.user.name)
  })
})
