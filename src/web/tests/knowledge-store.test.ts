// @vitest-environment node
import { afterEach, describe, expect, test } from 'vitest'
import { promises as fs } from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { createKnowledgeStore } from '@/lib/knowledge/files'

const tmps: string[] = []

async function mkstore() {
  const root = await fs.mkdtemp(path.join(os.tmpdir(), 'jarvis-knowledge-'))
  tmps.push(root)
  return {
    root,
    store: createKnowledgeStore({
      root,
      blockHeader: 'Personal knowledge',
      blockIntro: 'Reference material.',
    }),
  }
}

afterEach(async () => {
  for (const d of tmps.splice(0)) await fs.rm(d, { recursive: true, force: true })
})

describe('knowledge store factory', () => {
  test('add → list → readBlock round-trip', async () => {
    const { store } = await mkstore()
    const r = await store.add('notes.md', 'JARVIS is voice-first.')
    expect(r.ok).toBe(true)
    const docs = await store.list()
    expect(docs).toHaveLength(1)
    expect(docs[0].name).toBe('notes.md')
    expect(docs[0].enabled).toBe(true)
    const block = await store.readBlock()
    expect(block).toContain('## Personal knowledge')
    expect(block).toContain('JARVIS is voice-first.')
  })

  test('disabled docs are excluded from the block; empty when all disabled', async () => {
    const { store } = await mkstore()
    await store.add('a.md', 'alpha')
    await store.add('b.md', 'beta')
    expect(await store.setEnabled('a.md', false)).toBe(true)
    const block = await store.readBlock()
    expect(block).not.toContain('alpha')
    expect(block).toContain('beta')
    await store.setEnabled('b.md', false)
    expect(await store.readBlock()).toBe('')
  })

  test('remove deletes the file and clears the disabled entry', async () => {
    const { store, root } = await mkstore()
    await store.add('gone.md', 'x')
    await store.setEnabled('gone.md', false)
    expect(await store.remove('gone.md')).toBe(true)
    expect(await store.list()).toHaveLength(0)
    await expect(fs.access(path.join(root, 'gone.md'))).rejects.toThrow()
  })

  test('unsafe names: dotfiles rejected, traversal confined to root', async () => {
    const { store, root } = await mkstore()
    expect((await store.add('.hidden', 'x')).ok).toBe(false)
    expect((await store.add('', 'x')).ok).toBe(false)
    // basename() strips the traversal — the file must land INSIDE root.
    const r = await store.add('../escape.md', 'contained')
    expect(r.ok).toBe(true)
    await expect(fs.access(path.join(root, 'escape.md'))).resolves.toBeUndefined()
    await expect(fs.access(path.join(root, '..', 'escape.md'))).rejects.toThrow()
  })
})
