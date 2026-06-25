import { NextResponse } from 'next/server'
import { getStore } from '@/lib/bridge/db'
import { createGroup, listGroups } from '@/lib/bridge/store'
import { getUserId } from '@/lib/auth-helpers'
import { bridgeError } from '@/lib/bridge/errors'

// Session groups for the /code sidebar "Move to group". Session-cookie
// authenticated (same-origin UI); groups are per-user.

// GET /api/bridge/v1/groups — the user's groups.
export async function GET(req: Request): Promise<NextResponse> {
  try {
    const userId = await getUserId(req.headers)
    if (!userId) return bridgeError(401, 'unauthenticated', 'Sign in required')
    const groups = listGroups(getStore(), userId).map((g) => ({
      group_id: g.group_id,
      name: g.name,
    }))
    return NextResponse.json({ groups })
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err)
    return bridgeError(500, 'internal_error', `DB error: ${msg}`)
  }
}

// POST /api/bridge/v1/groups { name } — create a group, returns { group_id }.
export async function POST(req: Request): Promise<NextResponse> {
  const body = (await req.json().catch(() => null)) as { name?: string } | null
  const name = typeof body?.name === 'string' ? body.name.trim() : ''
  if (!name) return bridgeError(400, 'invalid_request', 'name required')
  try {
    const userId = await getUserId(req.headers)
    if (!userId) return bridgeError(401, 'unauthenticated', 'Sign in required')
    const groupId = createGroup(getStore(), name.slice(0, 60), userId)
    return NextResponse.json({ group_id: groupId, name }, { status: 201 })
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err)
    return bridgeError(500, 'internal_error', `DB error: ${msg}`)
  }
}
