// @vitest-environment node
import { afterEach, describe, expect, test } from 'vitest'
import { promises as fs } from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import {
  deleteSkill,
  listSkills,
  parseSkillFrontmatter,
  saveSkill,
} from '@/lib/skills'

const tmps: string[] = []

async function mkroot() {
  // Parent dir so the trash root (sibling of the skills root) stays inside
  // the tmp tree — mirrors ~/.jarvis/skills vs ~/.jarvis/.skills-trash.
  const parent = await fs.mkdtemp(path.join(os.tmpdir(), 'jarvis-skills-'))
  tmps.push(parent)
  const root = path.join(parent, 'skills')
  await fs.mkdir(root, { recursive: true })
  return { parent, root }
}

afterEach(async () => {
  for (const d of tmps.splice(0)) await fs.rm(d, { recursive: true, force: true })
})

describe('personal skills store', () => {
  test('save → list round-trip in the CLI/voice interop layout', async () => {
    const { root } = await mkroot()
    const r = await saveSkill(
      {
        name: 'release-notes',
        description: 'Summarize git log into release notes',
        whenToUse: 'User asks for release notes',
        body: '# Release notes\n\nRun `git log` and summarize.',
      },
      root,
    )
    expect(r.ok).toBe(true)

    // Interop layout: <root>/<name>/SKILL.md (the ONLY layout the CLI loads;
    // also the voice agent's preferred layout).
    const file = path.join(root, 'release-notes', 'SKILL.md')
    const raw = await fs.readFile(file, 'utf8')
    // Frontmatter shape mirrors voice skills_authoring.py::render_skill_md.
    expect(raw).toBe(
      '---\n' +
        'name: release-notes\n' +
        'description: Summarize git log into release notes\n' +
        'when_to_use: User asks for release notes\n' +
        '---\n\n' +
        '# Release notes\n\nRun `git log` and summarize.\n',
    )

    const skills = await listSkills(root)
    expect(skills).toHaveLength(1)
    expect(skills[0]).toMatchObject({
      name: 'release-notes',
      description: 'Summarize git log into release notes',
      whenToUse: 'User asks for release notes',
      layout: 'dir',
    })
  })

  test('multi-line when_to_use renders as block scalar and parses back', async () => {
    const { root } = await mkroot()
    const wtu = 'User wants music control\n(play / pause / next).'
    const r = await saveSkill(
      { name: 'music', description: 'Control music', whenToUse: wtu, body: 'b' },
      root,
    )
    expect(r.ok).toBe(true)
    const raw = await fs.readFile(path.join(root, 'music', 'SKILL.md'), 'utf8')
    expect(raw).toContain('when_to_use: |\n  User wants music control\n  (play / pause / next).')
    const { meta, body } = parseSkillFrontmatter(raw)
    expect(meta.when_to_use).toBe(wtu)
    expect(meta.name).toBe('music')
    expect(body.trim()).toBe('b')
  })

  test('validation: bad names, empty fields, newline description collapsed', async () => {
    const { root } = await mkroot()
    expect((await saveSkill({ name: '../evil', description: 'd', body: 'b' }, root)).ok).toBe(false)
    expect((await saveSkill({ name: 'Has Spaces', description: 'd', body: 'b' }, root)).ok).toBe(false)
    expect((await saveSkill({ name: 'ok', description: '', body: 'b' }, root)).ok).toBe(false)
    expect((await saveSkill({ name: 'ok', description: 'd', body: ' ' }, root)).ok).toBe(false)
    // Frontmatter injection: newlines in description collapse to spaces.
    const r = await saveSkill(
      { name: 'inj', description: 'a\n---\nname: pwned', body: 'b' },
      root,
    )
    expect(r.ok).toBe(true)
    const { meta } = parseSkillFrontmatter(
      await fs.readFile(path.join(root, 'inj', 'SKILL.md'), 'utf8'),
    )
    expect(meta.name).toBe('inj')
    expect(meta.description).toBe('a --- name: pwned')
  })

  test('lists flat <name>.md (voice legacy) and save upgrades it to dir layout', async () => {
    const { root } = await mkroot()
    await fs.writeFile(
      path.join(root, 'legacy.md'),
      '---\nname: legacy\ndescription: old flat skill\n---\n\nbody\n',
      'utf8',
    )
    let skills = await listSkills(root)
    expect(skills).toHaveLength(1)
    expect(skills[0]).toMatchObject({ name: 'legacy', layout: 'flat' })

    const r = await saveSkill(
      { name: 'legacy', description: 'now dir', body: 'body2' },
      root,
    )
    expect(r.ok).toBe(true)
    skills = await listSkills(root)
    expect(skills).toHaveLength(1)
    expect(skills[0].layout).toBe('dir')
    await expect(fs.access(path.join(root, 'legacy.md'))).rejects.toThrow()
  })

  test('delete is recoverable: moves to sibling .skills-trash, never rm', async () => {
    const { parent, root } = await mkroot()
    await saveSkill({ name: 'doomed', description: 'd', body: 'b' }, root)
    expect(await deleteSkill('doomed', root)).toBe(true)
    expect(await listSkills(root)).toHaveLength(0)
    const trash = await fs.readdir(path.join(parent, '.skills-trash'))
    expect(trash.some((n) => n.startsWith('doomed-'))).toBe(true)
    // Trashed content intact
    const inner = await fs.readFile(
      path.join(parent, '.skills-trash', trash[0], 'SKILL.md'),
      'utf8',
    )
    expect(inner).toContain('name: doomed')
    // Unknown / invalid names refuse
    expect(await deleteSkill('doomed', root)).toBe(false)
    expect(await deleteSkill('../escape', root)).toBe(false)
  })

  test('list tolerates rich hand-authored frontmatter (arrays, extra keys)', async () => {
    const { root } = await mkroot()
    const dir = path.join(root, 'autoplan')
    await fs.mkdir(dir)
    await fs.writeFile(
      path.join(dir, 'SKILL.md'),
      '---\nname: autoplan\npreamble-tier: 3\ndescription: Auto-review pipeline\ntriggers:\n  - run all reviews\nallowed-tools:\n  - Bash\n---\n\nbody\n',
      'utf8',
    )
    const skills = await listSkills(root)
    expect(skills).toHaveLength(1)
    expect(skills[0].description).toBe('Auto-review pipeline')
  })
})
