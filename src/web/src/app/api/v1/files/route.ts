import { NextResponse } from 'next/server'
import { getStore } from '@/lib/bridge/db'
import { resolveBridgeToken } from '@/lib/bridge/store'
import { extractBearer, isSharedLocalToken } from '@/lib/bridge/auth'
import { bridgeError } from '@/lib/bridge/errors'
import { writeBundle } from '@/lib/bridge/bundles'

// CCR-compat Files API (upload only) — the teleport/ultraplan client's
// createAndUploadGitBundle POSTs multipart here (file=_source_seed.bundle,
// plus a `purpose` field we ignore) and reads `{ id }` from the response,
// then passes the id as session_context.seed_bundle_file_id on
// POST /api/v1/sessions. That's what lets /ultraplan work from a LOCAL-ONLY
// git repo (no GitHub remote), like stock Claude Code's bundle mode.
// On the proxy's SELF_AUTH_POST allowlist, so this in-handler check is the
// only gate — same auth as POST /api/v1/sessions.
export const runtime = 'nodejs'
export const maxDuration = 300

// Bundle size cap. The CLI caps uploads at 500MB client-side; we're stricter —
// a >200MB bundle is a monorepo that should use a real remote.
const MAX_BUNDLE_BYTES = 200 * 1024 * 1024

export async function POST(req: Request): Promise<NextResponse> {
  const token = extractBearer(req.headers.get('authorization'))
  if (!token) return bridgeError(401, 'unauthorized', 'Missing bearer')
  const store = getStore()
  const userId = resolveBridgeToken(store, token)
  if (!userId && !isSharedLocalToken(token)) {
    return bridgeError(401, 'unauthorized', 'A valid bridge token is required')
  }
  // Reject oversized uploads BEFORE req.formData() buffers the body — the
  // post-parse file.size check alone means a 2GB POST is fully read into
  // memory before being refused (memory DoS). Content-Length can be absent
  // (chunked) or lie low, so the post-parse cap below stays as the
  // authoritative defense-in-depth check.
  const cl = Number(req.headers.get('content-length'))
  if (Number.isFinite(cl) && cl > MAX_BUNDLE_BYTES) {
    return bridgeError(413, 'file_too_large', `File exceeds ${MAX_BUNDLE_BYTES} bytes`)
  }
  let form: FormData
  try {
    form = await req.formData()
  } catch {
    return bridgeError(400, 'invalid_request', 'multipart/form-data body required')
  }
  const file = form.get('file')
  if (!file || typeof file === 'string') {
    return bridgeError(400, 'invalid_request', 'A "file" part is required')
  }
  if (file.size > MAX_BUNDLE_BYTES) {
    return bridgeError(413, 'file_too_large', `File exceeds ${MAX_BUNDLE_BYTES} bytes`)
  }
  const buf = Buffer.from(await file.arrayBuffer())
  try {
    const id = writeBundle(buf)
    // The CLI's uploadFile reads response.data.id (200 or 201 both accepted).
    return NextResponse.json({ id })
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err)
    return bridgeError(500, 'internal_error', `Bundle store failed: ${msg}`)
  }
}
