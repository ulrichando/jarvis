import { describe, expect, test, vi } from 'vitest'

vi.mock('@/lib/connectors/github', () => ({
  getPrDiff: vi.fn(async () => 'diff --git a/x.ts b/x.ts\n+const a = 1\n'),
  postPrComment: vi.fn(async () => ({ ok: true, url: 'https://github.com/o/r/pull/1#issuecomment-1' })),
}))
vi.mock('@/lib/ai/models', () => ({
  getModel: vi.fn(async () => ({ meta: {}, model: {} })),
}))
vi.mock('ai', () => ({
  generateText: vi.fn(async () => ({ text: 'Important: x.ts:1 — looks fine.' })),
}))

describe('reviewPullRequest', () => {
  test('reviews the diff and posts a PR comment', async () => {
    const { reviewPullRequest } = await import('@/lib/bridge/code-review')
    const r = await reviewPullRequest('o/r', 1)
    expect(r.ok).toBe(true)
    if (r.ok) expect(r.url).toContain('#issuecomment')
  })

  test('errors when the PR has no diff', async () => {
    const gh = await import('@/lib/connectors/github')
    vi.mocked(gh.getPrDiff).mockResolvedValueOnce(null)
    const { reviewPullRequest } = await import('@/lib/bridge/code-review')
    const r = await reviewPullRequest('o/r', 2)
    expect(r.ok).toBe(false)
  })
})
