import { NextResponse } from 'next/server'
import { getStore } from '@/lib/bridge/db'
import { createApiToken, listApiTokens, revokeApiToken } from '@/lib/bridge/store'
import { getUserId } from '@/lib/auth-helpers'
import { bridgeError } from '@/lib/bridge/errors'

export const runtime = 'nodejs'

// User API tokens (Settings → API Tokens): the logged-in web user manages
// long-lived bearer tokens for programmatic access to the Jarvis API — the CLI
// (`jarvis auth login --token`), scripts, `curl`. These are named
// bridge_tokens rows, so they authenticate on every route that already accepts
// a bridge token (keys, sessions, teleport, …). Cookie-authed: only the signed-
// in owner can see or mutate their own tokens; the secret is returned ONCE, on
// create.

const NAME_RE = /^[\w .-]{1,60}$/

export async function GET(req: Request): Promise<NextResponse> {
  const userId = await getUserId(req.headers)
  if (!userId) return bridgeError(401, 'unauthenticated', 'Sign in required')
  return NextResponse.json({ tokens: listApiTokens(getStore(), userId) })
}

export async function POST(req: Request): Promise<NextResponse> {
  const userId = await getUserId(req.headers)
  if (!userId) return bridgeError(401, 'unauthenticated', 'Sign in required')
  const body = (await req.json().catch(() => null)) as { name?: string } | null
  const name = typeof body?.name === 'string' ? body.name.trim() : ''
  if (!NAME_RE.test(name)) {
    return bridgeError(400, 'invalid_request', 'name must be 1–60 chars (letters, numbers, space . _ -)')
  }
  const token = createApiToken(getStore(), userId, name)
  if (!token) {
    return bridgeError(409, 'conflict', `You already have a token named "${name}"`)
  }
  // The one and only time the full secret is exposed.
  return NextResponse.json({ name, token }, { status: 201 })
}

export async function DELETE(req: Request): Promise<NextResponse> {
  const userId = await getUserId(req.headers)
  if (!userId) return bridgeError(401, 'unauthenticated', 'Sign in required')
  const body = (await req.json().catch(() => null)) as { name?: string } | null
  const name = typeof body?.name === 'string' ? body.name.trim() : ''
  if (!name) return bridgeError(400, 'invalid_request', 'name is required')
  const removed = revokeApiToken(getStore(), userId, name)
  if (!removed) return bridgeError(404, 'not_found', 'No such token')
  return NextResponse.json({ revoked: name })
}
