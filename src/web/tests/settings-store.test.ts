// @vitest-environment node
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest'
import { promises as fs } from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { DEFAULT_SETTINGS } from '@/lib/settings/schema'
import { DEFAULT_MODEL } from '@/lib/ai/models-meta'

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

  // Regression: a model id that was valid when saved but has since been
  // removed from MODELS_META (live case: a stale id left behind by a
  // provider removal) must NOT discard the whole file — before the schema
  // .catch() fix, loadSettings silently served DEFAULT_SETTINGS, dropping
  // stored API keys, and the next save destroyed them on disk.
  test('salvages file with stale model id — keys survive, model coerces', async () => {
    const home = await mktmp()
    const cwd = await mktmp()
    await fs.mkdir(path.join(home, '.jarvis'), { recursive: true })
    await fs.writeFile(
      path.join(home, '.jarvis', 'settings.json'),
      JSON.stringify({
        ...DEFAULT_SETTINGS,
        defaults: { ...DEFAULT_SETTINGS.defaults, model: 'llama-3.3-70b' },
        providers: {
          ...DEFAULT_SETTINGS.providers,
          openai: { apiKey: 'sk-keep-me' },
        },
      }),
    )
    const { loadSettings } = await loadStore(home, cwd)
    const loaded = await loadSettings()
    expect(loaded.providers.openai.apiKey).toBe('sk-keep-me')
    expect(loaded.defaults.model).toBe(DEFAULT_MODEL)
  })
})

// ~/.jarvis/settings.json is SHARED with the jarvis CLI + voice-agent, which
// store their own keys in the same file. Regression guard for the data-loss
// bug where every web save rewrote the file through the web-only zod schema
// (which strips unknown keys) and silently deleted the CLI/voice keys.
describe('shared settings.json — CLI/voice keys survive web writes', () => {
  // Representative keys the CLI actually stores (observed in the live bug).
  const CLI_KEYS = {
    enabledPlugins: ['workflow', 'gstack'],
    effortLevel: 'high',
    skipDangerousModePermissionPrompt: true,
    voiceNoticeSeen: true,
  }

  async function seedShared(home: string) {
    await fs.mkdir(path.join(home, '.jarvis'), { recursive: true })
    await fs.writeFile(
      path.join(home, '.jarvis', 'settings.json'),
      JSON.stringify(
        {
          ...DEFAULT_SETTINGS,
          // nested unknown key inside a web-known object
          user: { ...DEFAULT_SETTINGS.user, name: 'Ulrich', cliNickname: 'boss' },
          providers: {
            ...DEFAULT_SETTINGS.providers,
            anthropic: { apiKey: 'sk-ant-keep' },
          },
          ...CLI_KEYS,
        },
        null,
        2,
      ),
    )
  }

  async function readDisk(home: string) {
    return JSON.parse(
      await fs.readFile(path.join(home, '.jarvis', 'settings.json'), 'utf-8'),
    )
  }

  function expectCliKeysIntact(disk: Record<string, unknown>) {
    expect(disk.enabledPlugins).toEqual(CLI_KEYS.enabledPlugins)
    expect(disk.effortLevel).toBe(CLI_KEYS.effortLevel)
    expect(disk.skipDangerousModePermissionPrompt).toBe(true)
    expect(disk.voiceNoticeSeen).toBe(true)
  }

  // Route handlers import the store transitively — same reset+reimport dance.
  async function loadRoute(home: string, cwd: string) {
    vi.resetModules()
    vi.stubEnv('HOME', home)
    process.chdir(cwd)
    return await import('@/app/api/settings/route')
  }

  test('saveSettings preserves unknown top-level AND nested keys', async () => {
    const home = await mktmp()
    const cwd = await mktmp()
    await seedShared(home)
    const { loadSettings, saveSettings } = await loadStore(home, cwd)
    const s = await loadSettings()
    await saveSettings({ ...s, user: { ...s.user, name: 'Renamed' } })
    const disk = await readDisk(home)
    expectCliKeysIntact(disk)
    expect(disk.user.cliNickname).toBe('boss') // nested unknown survives
    expect(disk.user.name).toBe('Renamed') // web change applied
    expect(disk.providers.anthropic.apiKey).toBe('sk-ant-keep')
  })

  test('PATCH via the route updates the web key, CLI keys survive on disk', async () => {
    const home = await mktmp()
    const cwd = await mktmp()
    await seedShared(home)
    const route = await loadRoute(home, cwd)
    const res = await route.PATCH(
      new Request('http://localhost/api/settings', {
        method: 'PATCH',
        body: JSON.stringify({ user: { name: 'FromWeb' } }),
      }),
    )
    expect(res.status).toBe(200)
    const disk = await readDisk(home)
    expectCliKeysIntact(disk)
    expect(disk.user.cliNickname).toBe('boss')
    expect(disk.user.name).toBe('FromWeb')
    expect(disk.providers.anthropic.apiKey).toBe('sk-ant-keep')
  })

  test('PATCH clearing a provider key does NOT resurrect it from disk', async () => {
    const home = await mktmp()
    const cwd = await mktmp()
    await seedShared(home)
    const route = await loadRoute(home, cwd)
    const res = await route.PATCH(
      new Request('http://localhost/api/settings', {
        method: 'PATCH',
        body: JSON.stringify({ providers: { anthropic: { apiKey: null } } }),
      }),
    )
    expect(res.status).toBe(200)
    const disk = await readDisk(home)
    // web-owned key deliberately cleared — the graft must not restore it
    expect(disk.providers.anthropic.apiKey).toBeUndefined()
    expectCliKeysIntact(disk) // …while foreign keys still survive
  })

  test('DELETE (reset) resets web keys only — CLI keys + secrets survive', async () => {
    const home = await mktmp()
    const cwd = await mktmp()
    await seedShared(home)
    const route = await loadRoute(home, cwd)
    const res = await route.DELETE()
    expect(res.status).toBe(200)
    const disk = await readDisk(home)
    expect(disk.user.name).toBeUndefined() // web-owned key reset
    expect(disk.user.cliNickname).toBe('boss') // nested foreign key kept
    expect(disk.providers.anthropic.apiKey).toBe('sk-ant-keep') // secrets kept
    expectCliKeysIntact(disk)
  })

  test('GET does not leak unknown CLI keys to the client', async () => {
    const home = await mktmp()
    const cwd = await mktmp()
    await seedShared(home)
    const route = await loadRoute(home, cwd)
    const body = await (await route.GET()).json()
    expect(body.enabledPlugins).toBeUndefined()
    expect(body.effortLevel).toBeUndefined()
    expect(body.user.cliNickname).toBeUndefined()
  })
})
