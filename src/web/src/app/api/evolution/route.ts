/**
 * GET /api/evolution — list pending self-evolution proposals for review.
 *
 * Reads the auto-mod artifacts JARVIS writes to ~/.jarvis/auto-mods/*.json and
 * returns the pending ones (the changes JARVIS has proposed to its own code).
 * The /evolution page renders these as review cards; approving one POSTs to
 * /api/evolution/[id]/approve, which runs the host-side deploy + arms the
 * watchdog. Same-origin from the logged-in page (proxy.ts gates it).
 */
import { promises as fs } from 'fs'
import os from 'os'
import path from 'path'
import { execFile } from 'child_process'
import { promisify } from 'util'
import Database from 'better-sqlite3'

const execFileP = promisify(execFile)

export const dynamic = 'force-dynamic'
export const runtime = 'nodejs'

const AUTOMOD_DIR = path.join(os.homedir(), '.jarvis', 'auto-mods')
const EVOLUTION_LOG = path.join(os.homedir(), '.jarvis', 'evolution_log.jsonl')
const NIGHTLY_LOG = path.join(os.homedir(), '.local', 'share', 'jarvis', 'logs', 'evolution-nightly.log')
// Fitness readings the soak gate appends (one per scored day). Schema mirrors
// src/voice-agent/evolution/ledger.py::readings.
const LEDGER_DB = path.join(os.homedir(), '.local', 'share', 'jarvis', 'evolution_ledger.db')
const THROTTLE_FILE = path.join(AUTOMOD_DIR, 'throttle.json')
const ACTIVE_DEPLOY_FILE = path.join(AUTOMOD_DIR, 'active-deploy.json')
const AUTO_FLAG_FILE = path.join(AUTOMOD_DIR, '.evolution-auto')
const DAILY_CAP = Number(process.env.JARVIS_AUTOMOD_DAILY_CAP ?? '5') || 5
const TAIL_BYTES = 180_000

type ProposalPayload = {
  id: string
  title: string
  intent: string
  files: string[]
  diffSummary: string
  diff: string
  diffTruncated: boolean
  testsOk: boolean
  prUrl: string | null
  createdAt: string | null
  status: string
  rejectionReason: string
  testOutput: string
  coverageGate: { status: string; score: number | null; covered: number; measurable: number }
  priority: string
  evolution: Record<string, unknown>
}

type ActivityPayload = {
  id: string
  status: string
  kind: string
  title: string
  detail: string
  createdAt: string | null
  automodId?: string
  priority?: string
  source?: string
  rollbackSha?: string
  rollbackRef?: string
  mergeSha?: string
}

type TimeFramePayload = {
  id: string
  status: string
  summary: string
  startedAt: string | null
  endedAt: string | null
  durationMs: number | null
  eventCount: number
}

// Canonical evolution criteria. The five `pillar` rows are the Darwinian
// principles guaranteed per proposal — they MUST stay in sync with
// src/voice-agent/pipeline/automod/criteria.py::_FULL_SATISFIED. The three
// `system` rows are properties of the loop as a whole (not per-proposal flags).
const CRITERIA = [
  {
    id: 'variation',
    group: 'pillar',
    label: 'Variation',
    description: 'A concrete behavior or source-code variant is proposed.',
  },
  {
    id: 'selection',
    group: 'pillar',
    label: 'Selection',
    description: 'Tests, diff gates, and review decide whether it survives.',
  },
  {
    id: 'inheritance',
    group: 'pillar',
    label: 'Inheritance',
    description: 'Approved variants become the next source baseline.',
  },
  {
    id: 'feedback',
    group: 'pillar',
    label: 'Feedback',
    description: 'Telemetry, corrections, failures, or explicit user pressure drive change.',
  },
  {
    id: 'safety',
    group: 'pillar',
    label: 'Safety',
    description: 'Blocklists, approval, watchdog health checks, and rollback bound risk.',
  },
  {
    id: 'visibility',
    group: 'system',
    label: 'Visibility',
    description: 'Every queued, building, failed, pending, and deployed state is reviewable here.',
  },
  {
    id: 'autonomy',
    group: 'system',
    label: 'Bounded autonomy',
    description: 'JARVIS may detect, queue, and draft changes; a human approves every deploy until proven.',
  },
  {
    id: 'perfection',
    group: 'system',
    label: 'Perfection target',
    description: 'Each proposal should move reliability, capability, truthfulness, latency, safety, or alignment forward without regressions.',
  },
]

const AUTONOMY = {
  currentStage: 'human_review_required',
  currentLabel: 'Human review required',
  targetStage: 'fully_autonomous_when_proven',
  targetLabel: 'Fully autonomous when proven',
  graduationCriteria: [
    'sustained green proposal test history',
    'no watchdog rollbacks over a long window',
    'no safety/blocklist violations',
    'measurable reliability and latency improvement',
    'approval history shows consistently correct changes',
  ],
}

function testsOk(tail: string): boolean {
  const low = (tail || '').toLowerCase()
  return !!low && low.includes('passed') && !low.includes('failed') && !low.includes('error')
}

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {}
}

async function readTailLines(file: string, maxLines: number): Promise<string[]> {
  try {
    const stat = await fs.stat(file)
    const start = Math.max(0, stat.size - TAIL_BYTES)
    const handle = await fs.open(file, 'r')
    try {
      const length = stat.size - start
      const buffer = Buffer.alloc(length)
      await handle.read(buffer, 0, length, start)
      return buffer
        .toString('utf-8')
        .split(/\r?\n/)
        .map((line) => line.trim())
        .filter(Boolean)
        .slice(-maxLines)
    } finally {
      await handle.close()
    }
  } catch {
    return []
  }
}

async function readQueue(): Promise<ActivityPayload[]> {
  const lines = await readTailLines(path.join(AUTOMOD_DIR, 'queue.jsonl'), 50)
  return lines.flatMap((line) => {
    try {
      const rec = asRecord(JSON.parse(line))
      const intent = String(rec.intent ?? '').trim()
      return [{
        id: String(rec.id ?? 'queued'),
        status: 'queued',
        kind: String(rec.kind ?? 'intent'),
        title: (intent.split('\n')[0] || 'Queued self-evolution intent').slice(0, 100),
        detail: String(rec.rationale ?? intent),
        createdAt: typeof rec.created_at === 'string' ? rec.created_at : null,
        automodId: String(rec.id ?? ''),
        priority: typeof rec.priority === 'string' ? rec.priority : undefined,
      }]
    } catch {
      return []
    }
  })
}

type DeployedPayload = {
  id: string
  title: string
  intent: string
  mergeSha: string
  rollbackSha: string
  rollbackRef: string
  createdAt: string | null
  canRevert: boolean
  diff: string
  diffTruncated: boolean
  files: string[]
  priority: string
}

async function readArtifacts(): Promise<{
  proposals: ProposalPayload[]
  failed: ProposalPayload[]
  deployed: DeployedPayload[]
  artifactActivity: ActivityPayload[]
}> {
  let names: string[]
  try {
    names = (await fs.readdir(AUTOMOD_DIR)).filter(
      (f) => f.startsWith('automod-') && f.endsWith('.json') && !f.endsWith('.review.json'),
    )
  } catch {
    return { proposals: [], failed: [], deployed: [], artifactActivity: [] }
  }

  const proposals: ProposalPayload[] = []
  const failed: ProposalPayload[] = []
  const deployed: DeployedPayload[] = []
  const artifactActivity: ActivityPayload[] = []
  for (const name of names) {
    try {
      const art = JSON.parse(
        await fs.readFile(path.join(AUTOMOD_DIR, name), 'utf-8'),
      )
      const rec = asRecord(art)
      const status = String(rec.status ?? 'unknown')
      const intent = String(art.intent ?? '').trim()
      const id = String(rec.id ?? name.replace(/\.json$/, ''))
      const item: ProposalPayload = {
        id,
        title: (intent.split('\n')[0] || 'Self-evolution proposal').slice(0, 100),
        intent,
        files: Array.isArray(rec.files_changed) ? rec.files_changed.map(String) : [],
        diffSummary: String(rec.diff_summary ?? '').trim(),
        diff: typeof rec.diff === 'string' ? rec.diff : '',
        diffTruncated: !!rec.diff_truncated,
        testsOk: testsOk(String(art.test_output_tail ?? '')),
        prUrl: typeof rec.pr_url === 'string' ? rec.pr_url : null,
        createdAt: typeof rec.created_at === 'string' ? rec.created_at : null,
        status,
        rejectionReason: String(rec.rejection_reason ?? '').trim(),
        testOutput: String(rec.test_output_tail ?? '').trim(),
        coverageGate: (() => {
          const cg = asRecord(rec.coverage_gate)
          return {
            status: String(cg.status ?? ''),
            score: typeof cg.score === 'number' ? cg.score : null,
            covered: Number(cg.covered ?? 0) || 0,
            measurable: Number(cg.measurable ?? 0) || 0,
          }
        })(),
        priority: typeof rec.priority === 'string' ? rec.priority : 'P3',
        evolution: asRecord(rec.evolution),
      }
      if (status === 'pending') {
        proposals.push(item)
      } else if (status === 'failed' || status === 'rejected') {
        failed.push(item)
      } else if (status === 'merged') {
        const mergeSha = String(rec.merge_sha ?? '')
        const rollbackSha = String(rec.rollback_sha ?? '')
        const rollbackRef = String(rec.rollback_ref ?? '')
        deployed.push({
          id,
          title: item.title,
          intent,
          mergeSha,
          rollbackSha,
          rollbackRef,
          createdAt: typeof rec.auto_merged_at === 'string' ? rec.auto_merged_at : item.createdAt,
          // revertible if we recorded a rollback ref/sha or a merge sha
          canRevert: !!(rollbackRef || rollbackSha || mergeSha),
          diff: item.diff,
          diffTruncated: item.diffTruncated,
          files: item.files,
          priority: item.priority,
        })
      }
      artifactActivity.push({
        id,
        status,
        kind: String(asRecord(rec.evolution).fitness_goal_label ?? rec.kind ?? 'artifact'),
        title: item.title,
        detail: status === 'failed'
          ? String(rec.rejection_reason ?? rec.diff_summary ?? 'Proposal failed')
          : String(rec.diff_summary ?? ''),
        createdAt: item.createdAt,
        automodId: id,
      })
    } catch {
      /* skip an unreadable / malformed artifact */
    }
  }
  const byNewest = (a: ProposalPayload, b: ProposalPayload) =>
    (b.createdAt ?? '').localeCompare(a.createdAt ?? '')
  proposals.sort(byNewest)
  failed.sort(byNewest)
  deployed.sort((a, b) => (b.createdAt ?? '').localeCompare(a.createdAt ?? ''))
  artifactActivity.sort((a, b) => (b.createdAt ?? '').localeCompare(a.createdAt ?? ''))
  return { proposals, failed, deployed, artifactActivity }
}

async function readPaused(): Promise<boolean> {
  try {
    await fs.access(path.join(AUTOMOD_DIR, '.evolution-paused'))
    return true
  } catch {
    return false
  }
}

async function readAutoMode(): Promise<boolean> {
  try {
    await fs.access(AUTO_FLAG_FILE)
    return true
  } catch {
    return false
  }
}

async function readBuildModel(): Promise<string> {
  // The model evolution builds run on (~/.jarvis/auto-mods/build-model).
  // Empty string = inherit the global cli-model.
  try {
    return (await fs.readFile(path.join(AUTOMOD_DIR, 'build-model'), 'utf-8')).trim()
  } catch {
    return ''
  }
}

async function readAuditActivity(): Promise<ActivityPayload[]> {
  const lines = await readTailLines(EVOLUTION_LOG, 80)
  return lines.flatMap((line) => {
    try {
      const rec = asRecord(JSON.parse(line))
      const kind = String(rec.kind ?? rec.event ?? '')
      if (!kind.startsWith('automod_') && !kind.startsWith('evolution_')) return []
      const id = String(rec.id ?? rec.automod_id ?? kind)
      return [{
        id: `${kind}:${id}:${String(rec.ts ?? '')}`,
        status: kind.replace(/^automod_/, '').replace(/^evolution_/, ''),
        kind,
        title: id,
        detail: String(rec.reason ?? rec.detail ?? rec.error ?? rec.exit_code ?? ''),
        createdAt: typeof rec.ts === 'string' ? rec.ts : null,
        automodId: id,
      }]
    } catch {
      return []
    }
  }).slice(-12).reverse()
}

async function readNightlyActivity(): Promise<ActivityPayload[]> {
  const lines = await readTailLines(NIGHTLY_LOG, 12)
  let logMtime: string | null = null
  try {
    logMtime = (await fs.stat(NIGHTLY_LOG)).mtime.toISOString()
  } catch {
    logMtime = null
  }
  return lines.flatMap((line, idx) => {
    try {
      const rec = asRecord(JSON.parse(line))
      const skipped = rec.skipped
      if (typeof skipped === 'string') {
        return [{
          id: `nightly:${idx}:${skipped}`,
          status: 'skipped',
          kind: 'periodic-scan',
          title: 'Periodic evolution skipped',
          detail: skipped,
          createdAt: logMtime,
        }]
      }
      return [{
        id: `nightly:${idx}`,
        status: 'run',
        kind: 'periodic-scan',
        title: 'Periodic evolution ran',
        detail: line,
        createdAt: logMtime,
      }]
    } catch {
      return []
    }
  }).reverse()
}

function buildTimeFrames(items: ActivityPayload[]): TimeFramePayload[] {
  const runs = new Map<string, {
    id: string
    status: string
    summary: string
    startedAt: number | null
    endedAt: number | null
    eventCount: number
  }>()

  for (const item of items) {
    const automodId = item.automodId?.trim()
    if (!automodId) continue
    const ts = item.createdAt ? Date.parse(item.createdAt) : Number.NaN
    const current = runs.get(automodId) ?? {
      id: automodId,
      status: item.status,
      summary: item.detail || item.title,
      startedAt: null,
      endedAt: null,
      eventCount: 0,
    }
    current.eventCount += 1
    if (!Number.isNaN(ts)) {
      if (current.startedAt === null || ts < current.startedAt) {
        current.startedAt = ts
      }
      if (current.endedAt === null || ts >= current.endedAt) {
        current.endedAt = ts
        current.status = item.status
        current.summary = item.detail || item.title
      }
    }
    runs.set(automodId, current)
  }

  return [...runs.values()]
    .map((run) => ({
      id: run.id,
      status: run.status,
      summary: run.summary,
      startedAt: run.startedAt === null ? null : new Date(run.startedAt).toISOString(),
      endedAt: run.endedAt === null ? null : new Date(run.endedAt).toISOString(),
      durationMs: run.startedAt === null || run.endedAt === null
        ? null
        : Math.max(0, run.endedAt - run.startedAt),
      eventCount: run.eventCount,
    }))
    .sort((a, b) => (
      new Date(b.endedAt ?? b.startedAt ?? 0).getTime()
      - new Date(a.endedAt ?? a.startedAt ?? 0).getTime()
    ))
    .slice(0, 12)
}

function shortSha(value: string): string {
  return value ? value.slice(0, 8) : ''
}

function actualRollbackEvent(item: ActivityPayload): boolean {
  const key = `${item.kind} ${item.status}`.toLowerCase()
  return key.includes('reverted') ||
    key.includes('rolled_back') ||
    key.includes('rollback_started') ||
    key.includes('rollback_failed')
}

async function readActiveDeployEvent(): Promise<ActivityPayload | null> {
  try {
    const rec = asRecord(JSON.parse(await fs.readFile(ACTIVE_DEPLOY_FILE, 'utf-8')))
    const automodId = String(rec.automod_id ?? rec.id ?? 'active-deploy')
    const rollbackSha = typeof rec.rollback_sha === 'string' ? rec.rollback_sha : ''
    const mergeSha = typeof rec.merge_sha === 'string' ? rec.merge_sha : ''
    const deployedAt = typeof rec.deployed_at === 'string' ? rec.deployed_at : null
    const deadline = typeof rec.deadline_ts === 'string'
      ? rec.deadline_ts
      : typeof rec.deadline_at === 'string'
        ? rec.deadline_at
        : null
    return {
      id: `active-deploy:${automodId}`,
      status: 'verifying',
      kind: 'active-deploy',
      title: automodId,
      detail: [
        rollbackSha ? `rollback ${shortSha(rollbackSha)}` : '',
        mergeSha ? `merge ${shortSha(mergeSha)}` : '',
        deadline ? `deadline ${deadline}` : '',
      ].filter(Boolean).join(' · ') || 'watchdog verification is in progress',
      createdAt: deployedAt,
      automodId,
      source: 'active-deploy.json',
      rollbackSha,
      mergeSha,
    }
  } catch {
    return null
  }
}

async function readRollbackEvents(deployed: DeployedPayload[]): Promise<ActivityPayload[]> {
  const out: ActivityPayload[] = []
  for (const line of await readTailLines(EVOLUTION_LOG, 300)) {
    try {
      const rec = asRecord(JSON.parse(line))
      const kind = String(rec.kind ?? rec.event ?? '')
      if (!/deploy|roll|revert/i.test(kind)) continue
      out.push({
        id: `${kind}:${String(rec.ts ?? '')}`,
        status: kind.replace(/^automod_/, '').replace(/^evolution_/, ''),
        kind,
        title: String(rec.id ?? rec.automod_id ?? kind),
        detail: String(rec.reason ?? rec.info ?? rec.rollback_sha ?? rec.merge_sha ?? rec.error ?? ''),
        createdAt: typeof rec.ts === 'string' ? rec.ts : null,
        automodId: String(rec.id ?? rec.automod_id ?? ''),
        source: 'evolution_log.jsonl',
        rollbackSha: typeof rec.rollback_sha === 'string' ? rec.rollback_sha : undefined,
        mergeSha: typeof rec.merge_sha === 'string' ? rec.merge_sha : undefined,
      })
    } catch {
      /* skip */
    }
  }

  const activeDeploy = await readActiveDeployEvent()
  if (activeDeploy) out.push(activeDeploy)

  for (const item of deployed) {
    if (!item.rollbackSha && !item.rollbackRef && !item.mergeSha) continue
    out.push({
      id: `rollback-point:${item.id}`,
      status: item.canRevert ? 'available' : 'missing',
      kind: 'rollback-point',
      title: item.id,
      detail: [
        item.rollbackSha ? `rollback ${shortSha(item.rollbackSha)}` : '',
        item.rollbackRef ? item.rollbackRef : '',
        item.mergeSha ? `merge ${shortSha(item.mergeSha)}` : '',
      ].filter(Boolean).join(' · '),
      createdAt: item.createdAt,
      automodId: item.id,
      source: 'automod artifact',
      rollbackSha: item.rollbackSha || undefined,
      rollbackRef: item.rollbackRef || undefined,
      mergeSha: item.mergeSha || undefined,
    })
  }

  const deduped = new Map<string, ActivityPayload>()
  for (const item of out) {
    const key = item.kind === 'rollback-point'
      ? item.id
      : `${item.kind}:${item.automodId ?? item.title}:${item.createdAt ?? item.detail}`
    deduped.set(key, item)
  }

  return [...deduped.values()]
    .sort((a, b) => (
      new Date(b.createdAt ?? 0).getTime()
      - new Date(a.createdAt ?? 0).getTime()
    ))
    .slice(0, 20)
}

async function readSelfAssessment(): Promise<unknown> {
  try {
    return JSON.parse(await fs.readFile(path.join(AUTOMOD_DIR, 'self_assessment.json'), 'utf-8'))
  } catch {
    return null
  }
}

// The 3-lens review council's verdict for one proposal (correctness / security /
// regression), written by pipeline/automod/review_council.py. Advisory only —
// it never gates a deploy; it informs the human's approve/reject decision.
async function readReview(id: string): Promise<unknown> {
  try {
    return JSON.parse(await fs.readFile(path.join(AUTOMOD_DIR, `${id}.review.json`), 'utf-8'))
  } catch {
    return null
  }
}

// Progress of a background "review all pending" run (written by
// review_all_pending) so the page can poll + update verdicts incrementally.
async function readReviewAllStatus(): Promise<unknown> {
  try {
    return JSON.parse(await fs.readFile(path.join(AUTOMOD_DIR, '.review-all-status.json'), 'utf-8'))
  } catch {
    return null
  }
}

type FitnessPoint = { ts: string; composite: number; passed: boolean }

function readFitness(): {
  points: FitnessPoint[]
  latest: number | null
  latestAt: string | null
  count: number
  trend: 'up' | 'down' | 'flat' | null
  perAxis: Record<string, number>
  weakAxis: { axis: string; score: number } | null
  source: string
  error?: string
} {
  const source = LEDGER_DB
  const empty = { points: [], latest: null, latestAt: null, count: 0, trend: null, perAxis: {}, weakAxis: null, source }
  try {
    const db = new Database(LEDGER_DB, { readonly: true, fileMustExist: true })
    try {
      const rows = db
        .prepare('SELECT ts_utc, composite, passed, per_axis_json FROM readings ORDER BY ts_utc DESC LIMIT 30')
        .all() as { ts_utc: string; composite: number; passed: number; per_axis_json: string }[]
      // SQL returns newest-first → reverse to chronological for the sparkline.
      const points: FitnessPoint[] = rows
        .map((r) => ({ ts: String(r.ts_utc), composite: Number(r.composite), passed: !!r.passed }))
        .reverse()
      const latest = points.length ? points[points.length - 1].composite : null
      const latestAt = points.length ? points[points.length - 1].ts : null
      let trend: 'up' | 'down' | 'flat' | null = null
      if (points.length >= 2 && latest !== null) {
        const d = latest - points[points.length - 2].composite
        trend = Math.abs(d) < 1e-6 ? 'flat' : d > 0 ? 'up' : 'down'
      }
      // Per-axis breakdown from the newest reading (rows[0]); weak axis = lowest.
      let perAxis: Record<string, number> = {}
      try {
        const raw = asRecord(JSON.parse(rows[0]?.per_axis_json ?? '{}'))
        for (const [k, v] of Object.entries(raw)) perAxis[k] = Number(v)
      } catch {
        perAxis = {}
      }
      const weakAxis = Object.entries(perAxis).reduce<{ axis: string; score: number } | null>(
        (lo, [axis, score]) => (lo === null || score < lo.score ? { axis, score } : lo),
        null,
      )
      return { points, latest, latestAt, count: points.length, trend, perAxis, weakAxis, source }
    } finally {
      db.close()
    }
  } catch (err) {
    return { ...empty, error: err instanceof Error ? err.message : String(err) }
  }
}

async function readThrottle(): Promise<{ today: number; cap: number; remaining: number }> {
  try {
    const rec = asRecord(JSON.parse(await fs.readFile(THROTTLE_FILE, 'utf-8')))
    const today =
      String(rec.date ?? '') === new Date().toISOString().slice(0, 10)
        ? Number(rec.admitted_today ?? 0) || 0
        : 0
    return { today, cap: DAILY_CAP, remaining: Math.max(0, DAILY_CAP - today) }
  } catch {
    return { today: 0, cap: DAILY_CAP, remaining: DAILY_CAP }
  }
}

async function readDeployStatus(): Promise<{ deployInFlight: boolean; rollbacks: number }> {
  let deployInFlight = false
  try {
    await fs.access(ACTIVE_DEPLOY_FILE)
    deployInFlight = true
  } catch {
    deployInFlight = false
  }
  let rollbacks = 0
  for (const line of await readTailLines(EVOLUTION_LOG, 200)) {
    try {
      const rec = asRecord(JSON.parse(line))
      const k = String(rec.kind ?? rec.event ?? '')
      if (k === 'automod_reverted' || k === 'evolution_rolled_back') rollbacks += 1
    } catch {
      /* skip */
    }
  }
  return { deployInFlight, rollbacks }
}

type InFlightBuild = { id: string; intent: string; kind: string; elapsedSec: number }

async function readInFlightBuilds(): Promise<{ count: number; builds: InFlightBuild[] }> {
  // Truth, not inference: a build is in-flight iff its `jarvis-automod-impl`
  // process is alive. (Counting ~/.jarvis/worktrees/automod-* over-counted —
  // failed builds leave the worktree behind, so e.g. 6 dead + 1 live read as
  // "7 building" forever.) Parse the intent id from the process argv, then
  // enrich with elapsed seconds (ps etimes) + the intent text/kind.
  try {
    const { stdout } = await execFileP('pgrep', ['-af', 'jarvis-automod-impl'], { timeout: 4000 })
    const seen = new Set<string>()
    const builds: InFlightBuild[] = []
    for (const line of stdout.split('\n')) {
      const idM = line.match(/automod-\d{4}-\d{2}-\d{2}-[0-9a-f]{6}/)
      const pidM = line.trim().match(/^(\d+)/)
      if (!idM || !pidM || seen.has(idM[0])) continue
      const id = idM[0]
      seen.add(id)
      let elapsedSec = 0
      try {
        const { stdout: et } = await execFileP('ps', ['-o', 'etimes=', '-p', pidM[1]], { timeout: 2000 })
        elapsedSec = parseInt(et.trim(), 10) || 0
      } catch { /* process may have just exited */ }
      let intent = ''
      let kind = ''
      try {
        const txt = await fs.readFile(
          path.join(AUTOMOD_DIR, `${id}.intent.txt`), 'utf8')
        intent = (txt.match(/^INTENT:\s*(.+)$/m)?.[1] ?? '').trim()
        kind = (txt.match(/^KIND:\s*(.+)$/m)?.[1] ?? '').trim()
      } catch { /* intent file may be gone */ }
      builds.push({ id, intent, kind, elapsedSec })
    }
    return { count: builds.length, builds }
  } catch {
    // pgrep exits non-zero when nothing matches → no builds in flight.
    return { count: 0, builds: [] }
  }
}

export async function GET(): Promise<Response> {
  const [artifacts, queued, audit, nightly, throttle, deployStatus, selfAssessment, paused, autoMode, buildModel, inFlight] =
    await Promise.all([
      readArtifacts(),
      readQueue(),
      readAuditActivity(),
      readNightlyActivity(),
      readThrottle(),
      readDeployStatus(),
      readSelfAssessment(),
      readPaused(),
      readAutoMode(),
      readBuildModel(),
      readInFlightBuilds(),
    ])
  const { proposals, failed, deployed, artifactActivity } = artifacts
  // Attach each pending proposal's 3-lens review council verdict (if reviewed).
  const proposalsReviewed = await Promise.all(
    proposals.map(async (p) => ({ ...p, review: await readReview(p.id) })),
  )
  const reviewAllStatus = await readReviewAllStatus()
  const rollbackEvents = await readRollbackEvents(deployed)
  const rollbackCount = rollbackEvents.filter(actualRollbackEvent).length
  const fitness = readFitness()

  // Graduation readiness — mirrors pipeline/automod/graduation.py::evaluate so
  // the human can SEE how close the loop is to relaxing human-gating. Display
  // only; the Python evaluator governs any actual auto-deploy (default OFF).
  const finalizedStatuses = artifactActivity.filter((a) =>
    ['pending', 'merged', 'failed'].includes(a.status),
  )
  const passedCount = finalizedStatuses.filter((a) => a.status !== 'failed').length
  const mergedCount = artifactActivity.filter((a) => a.status === 'merged').length
  const blocklistHits = artifactActivity.filter(
    (a) => a.status === 'failed' &&
      /blocklist|diff_validation_failed/i.test(a.detail || ''),
  ).length
  const greenRatio = finalizedStatuses.length ? passedCount / finalizedStatuses.length : 0
  const gradCriteria = [
    { id: 'green_history', label: 'Sustained green proposal history',
      met: finalizedStatuses.length >= 5 && greenRatio >= 0.8,
      detail: `${passedCount}/${finalizedStatuses.length} passed (need ≥5 at ≥80%)` },
    { id: 'no_rollbacks', label: 'No watchdog rollbacks in window',
      met: rollbackCount === 0,
      detail: `${rollbackCount} rollback(s)` },
    { id: 'no_blocklist', label: 'No safety/blocklist violations',
      met: blocklistHits === 0,
      detail: `${blocklistHits} blocklist/diff-validation rejection(s)` },
    { id: 'fitness', label: 'Measurable fitness, not regressing',
      met: fitness.latest !== null && fitness.latest >= 0.7 && fitness.trend !== 'down',
      detail: fitness.latest !== null ? `latest ${fitness.latest.toFixed(2)} (trend ${fitness.trend})` : 'no readings' },
    { id: 'correct_approvals', label: 'Consistently correct approvals',
      met: mergedCount >= 3 && rollbackCount === 0,
      detail: `${mergedCount} merged (need ≥3, 0 reverted)` },
  ]
  const graduation = {
    metCount: gradCriteria.filter((c) => c.met).length,
    total: gradCriteria.length,
    criteria: gradCriteria,
  }
  const allActivity = [...queued, ...nightly, ...audit, ...artifactActivity]
  const timeFrames = buildTimeFrames(allActivity)
  const activity = allActivity
    .sort((a, b) => (
      new Date(b.createdAt ?? 0).getTime()
      - new Date(a.createdAt ?? 0).getTime()
    ))
    .slice(0, 24)
  return Response.json({
    proposals: proposalsReviewed,
    reviewAll: reviewAllStatus,
    failed,
    deployed,
    queued,
    paused,
    autoMode,
    mode: autoMode ? 'auto' : 'manual',
    buildModel,
    activity,
    timeFrames,
    rollbackEvents,
    selfAssessment,
    criteria: CRITERIA,
    autonomy: AUTONOMY,
    graduation,
    fitness,
    status: {
      pending: proposals.length,
      queued: queued.length,
      failedCount: failed.length,
      deployed: artifactActivity.filter((a) => a.status === 'merged').length,
      failed: artifactActivity.filter((a) => a.status === 'failed').length,
      builds: throttle,
      autoMode,
      mode: autoMode ? 'auto' : 'manual',
      building: inFlight.count,
      buildingDetail: inFlight.builds,
      deployInFlight: deployStatus.deployInFlight,
      rollbacks: rollbackCount,
      recentActivity: activity.length,
      lastNightly: nightly[0] ?? null,
    },
  })
}
