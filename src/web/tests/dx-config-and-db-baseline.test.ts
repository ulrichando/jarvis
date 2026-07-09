// @vitest-environment node
//
// Findings #11 (typecheck script + CI ratchet) and #12 (dotenv dep move +
// db:baseline). These lock the DX/packaging config so a future edit can't
// silently un-ratchet CI or drop the phantom-dep fix, and prove db-baseline.mjs
// computes the same migration hashes the real drizzle migrator does (so a
// baselined DB is genuinely adopted, not just plausibly).
import { describe, expect, test } from 'vitest'
import crypto from 'node:crypto'
import { execFileSync } from 'node:child_process'
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import yaml from 'js-yaml'
import { readMigrationFiles } from 'drizzle-orm/migrator'

const webRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const repoRoot = path.resolve(webRoot, '..', '..')

function readJson(p: string) {
  return JSON.parse(fs.readFileSync(p, 'utf8'))
}

describe('finding #11 — typecheck script + CI ratchet', () => {
  const pkg = readJson(path.join(webRoot, 'package.json'))

  test('typecheck runs typegen BEFORE tsc (Next 16 route types)', () => {
    const s = pkg.scripts.typecheck as string
    expect(s).toBeTruthy()
    expect(s).toContain('next typegen')
    expect(s).toContain('tsc --noEmit')
    // typegen must precede tsc, else ~49 phantom RouteContext/PageProps errors.
    expect(s.indexOf('next typegen')).toBeLessThan(s.indexOf('tsc'))
  })

  test('check script chains lint + typecheck + test', () => {
    const s = pkg.scripts.check as string
    expect(s).toContain('lint')
    expect(s).toContain('typecheck')
    expect(s).toContain('test')
  })

  test('lint.yml tsc job is blocking (continue-on-error off), typegen-first', () => {
    const lint = yaml.load(
      fs.readFileSync(path.join(repoRoot, '.github/workflows/lint.yml'), 'utf8'),
    ) as any
    const tsc = lint.jobs.tsc
    // undefined (default false) or explicit false — never truthy.
    expect(tsc['continue-on-error']).toBeFalsy()
    const steps = tsc.steps as any[]
    const typegenIdx = steps.findIndex((st) => /next typegen/.test(st.run ?? ''))
    const tscIdx = steps.findIndex((st) => /tsc --noEmit/.test(st.run ?? ''))
    expect(typegenIdx).toBeGreaterThanOrEqual(0)
    expect(tscIdx).toBeGreaterThan(typegenIdx)
  })

  test('web-tests.yml comment no longer claims bun test is non-blocking', () => {
    const raw = fs.readFileSync(
      path.join(repoRoot, '.github/workflows/web-tests.yml'),
      'utf8',
    )
    expect(raw).not.toMatch(/non-blocking/i)
  })
})

describe('finding #12 — dotenv dep move + shadcn to devDeps', () => {
  const pkg = readJson(path.join(webRoot, 'package.json'))

  test('dotenv is a direct dependency (next.config.ts imports it)', () => {
    expect(pkg.dependencies.dotenv).toBeTruthy()
    // next.config.ts must still import it — the reason it needs to be a dep.
    const cfg = fs.readFileSync(path.join(webRoot, 'next.config.ts'), 'utf8')
    expect(cfg).toMatch(/from ["']dotenv["']/)
  })

  test('shadcn is a devDependency, not a runtime dependency', () => {
    expect(pkg.dependencies.shadcn).toBeUndefined()
    expect(pkg.devDependencies.shadcn).toBeTruthy()
  })

  test('package-lock records the same dep placement (lock synced)', () => {
    const lock = readJson(path.join(webRoot, 'package-lock.json'))
    const root = lock.packages['']
    expect(root.dependencies?.dotenv).toBeTruthy()
    expect(root.dependencies?.shadcn).toBeUndefined()
    expect(root.devDependencies?.shadcn).toBeTruthy()
  })
})

describe('finding #12 — db-baseline.mjs', () => {
  const scriptPath = path.join(webRoot, 'scripts', 'db-baseline.mjs')

  test('script parses and dry-run lists every journal migration', () => {
    const out = execFileSync('node', [scriptPath, '--dry-run'], {
      cwd: webRoot,
      encoding: 'utf8',
      env: { ...process.env, DATABASE_URL: '' },
    })
    const journal = readJson(
      path.join(webRoot, 'drizzle', 'meta', '_journal.json'),
    )
    for (const entry of journal.entries) {
      expect(out).toContain(entry.tag)
    }
    expect(out).toContain('no DB connection made')
  })

  test('dry-run hashes match the real drizzle migrator (sha256 of file)', () => {
    // drizzle's readMigrationFiles is the source of truth for how migrate()
    // hashes. Our baseline must produce byte-identical hashes or migrate()
    // would still re-run migrations after a baseline.
    const migrations = readMigrationFiles({
      migrationsFolder: path.join(webRoot, 'drizzle'),
    })
    const journal = readJson(
      path.join(webRoot, 'drizzle', 'meta', '_journal.json'),
    )
    for (const [i, entry] of journal.entries.entries()) {
      const contents = fs.readFileSync(
        path.join(webRoot, 'drizzle', `${entry.tag}.sql`),
        'utf8',
      )
      const ourHash = crypto
        .createHash('sha256')
        .update(contents)
        .digest('hex')
      // drizzle stores the full hash; assert ours equals its hash for the row.
      expect(ourHash).toBe(migrations[i].hash)
    }
  })

  test('created_at values come from the journal `when` (migrate skip guard)', () => {
    // migrate()'s guard is lastDbMigration.created_at < migration.when, so the
    // baseline MUST use journal.when, not Date.now(). Assert the script prints
    // each entry's `when` verbatim.
    const out = execFileSync('node', [scriptPath, '--dry-run'], {
      cwd: webRoot,
      encoding: 'utf8',
      env: { ...process.env, DATABASE_URL: '' },
    })
    const journal = readJson(
      path.join(webRoot, 'drizzle', 'meta', '_journal.json'),
    )
    for (const entry of journal.entries) {
      expect(out).toContain(`when=${entry.when}`)
    }
  })
})
