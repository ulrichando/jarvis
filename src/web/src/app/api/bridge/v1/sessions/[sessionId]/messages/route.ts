import { NextResponse } from 'next/server'
import { randomUUID } from 'node:crypto'
import { getStore } from '@/lib/bridge/db'
import {
  appendInbound,
  appendSessionEvent,
  findEnvironment,
  findSession,
} from '@/lib/bridge/store'
import { emitInbound } from '@/lib/bridge/events'
import { getUserId } from '@/lib/auth-helpers'
import { bridgeError } from '@/lib/bridge/errors'

// POST /api/bridge/v1/sessions/{id}/messages — the /code session view talks
// INTO a connected CLI session. Session-cookie authenticated (same-origin
// UI); ownership enforced against the session's environment. Three body
// kinds, all converted server-side into well-formed SDK messages (the CLI
// child hard-exits on a malformed stdin line, so raw client JSON is never
// forwarded):
//
//   {text}                          → user message
//   {interrupt: true}               → control_request {subtype:'interrupt'}
//   {permission: {request_id, behavior, updated_input?, message?}}
//                                   → control_response for a can_use_tool
//
// User messages are also mirrored into session_events as user_prompt so the
// UI shows them immediately (the worker's echo is deduped by uuid).
export async function POST(
  req: Request,
  ctx: { params: Promise<{ sessionId: string }> },
): Promise<NextResponse> {
  const { sessionId } = await ctx.params
  const body = (await req.json().catch(() => null)) as {
    text?: string
    interrupt?: boolean
    mode?: string
    model?: string
    images?: Array<{ media_type?: string; data?: string }>
    permission?: {
      request_id?: string
      behavior?: string
      updated_input?: Record<string, unknown>
      message?: string
    }
  } | null
  const text = typeof body?.text === 'string' ? body.text.trim() : ''
  const interrupt = body?.interrupt === true
  // Image attachments → Anthropic base64 image content blocks. Vision-capable
  // models see them; the proxy flattens to "[image]" for text-only models.
  const images = (Array.isArray(body?.images) ? body!.images : [])
    .filter(
      (im): im is { media_type: string; data: string } =>
        !!im &&
        typeof im.media_type === 'string' &&
        im.media_type.startsWith('image/') &&
        typeof im.data === 'string' &&
        im.data.length > 0,
    )
    .slice(0, 10)
  // ExternalPermissionMode in the CLI (types/permissions.ts) — applied live
  // via a set_permission_mode control_request (bridgeMessaging.ts:328).
  const VALID_MODES = ['default', 'acceptEdits', 'plan', 'bypassPermissions', 'dontAsk']
  const mode =
    typeof body?.mode === 'string' && VALID_MODES.includes(body.mode)
      ? body.mode
      : null
  const model =
    typeof body?.model === 'string' && body.model.trim() ? body.model.trim() : null
  const permission = body?.permission
  const permissionValid =
    !!permission &&
    typeof permission.request_id === 'string' &&
    !!permission.request_id &&
    (permission.behavior === 'allow' || permission.behavior === 'deny')
  if (!text && images.length === 0 && !interrupt && !mode && !model && !permissionValid) {
    return bridgeError(
      400,
      'invalid_request',
      'text, images, interrupt, mode, model, or permission {request_id, behavior} required',
    )
  }
  try {
    const store = getStore()
    const session = findSession(store, sessionId)
    if (!session) return bridgeError(404, 'not_found', 'Session not found')
    if (session.archived) {
      return bridgeError(409, 'archived', 'Session is archived')
    }
    const env = session.environment_id
      ? findEnvironment(store, session.environment_id)
      : null
    const userId = await getUserId(req.headers)
    if (env?.user_id && env.user_id !== userId) {
      // No valid session (getUserId → null) against a real-owned session is
      // "your login expired", not "someone else's session" — answer 401 so the
      // client can prompt a re-login instead of a dead-end 403 that reads as a
      // silent unresponsive chat. A genuine cross-user mismatch (two real
      // accounts) still returns 403.
      if (userId === null) {
        return bridgeError(401, 'unauthenticated', 'Session expired — please sign in again')
      }
      return bridgeError(403, 'forbidden', 'Not your session')
    }
    const uuid = randomUUID()
    if (text || images.length) {
      const content: Array<Record<string, unknown>> = []
      if (text) content.push({ type: 'text', text })
      for (const im of images) {
        content.push({
          type: 'image',
          source: { type: 'base64', media_type: im.media_type, data: im.data },
        })
      }
      appendInbound(store, sessionId, {
        type: 'user',
        uuid,
        session_id: sessionId,
        parent_tool_use_id: null,
        message: { role: 'user', content },
      })
      appendSessionEvent(store, sessionId, {
        type: 'user_prompt',
        payload: {
          type: 'user_prompt',
          prompt: text || `🖼 ${images.length} image${images.length === 1 ? '' : 's'}`,
          uuid,
        },
      })
    } else if (interrupt) {
      appendInbound(store, sessionId, {
        type: 'control_request',
        uuid,
        request_id: uuid,
        request: { subtype: 'interrupt' },
      })
    } else if (mode) {
      appendInbound(store, sessionId, {
        type: 'control_request',
        uuid,
        request_id: uuid,
        request: { subtype: 'set_permission_mode', mode },
      })
    } else if (model) {
      appendInbound(store, sessionId, {
        type: 'control_request',
        uuid,
        request_id: uuid,
        request: { subtype: 'set_model', model },
      })
    } else if (permission && permissionValid) {
      appendInbound(store, sessionId, {
        type: 'control_response',
        uuid,
        response: {
          subtype: 'success',
          request_id: permission.request_id,
          response:
            permission.behavior === 'allow'
              ? {
                  behavior: 'allow',
                  updatedInput:
                    permission.updated_input &&
                    typeof permission.updated_input === 'object'
                      ? permission.updated_input
                      : {},
                }
              : {
                  behavior: 'deny',
                  message:
                    typeof permission.message === 'string' &&
                    permission.message.trim()
                      ? permission.message.trim()
                      : 'Denied from the web session view',
                },
        },
      })
    }
    emitInbound(sessionId)
    return NextResponse.json({ ok: true, uuid })
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err)
    return bridgeError(500, 'internal_error', `DB error: ${msg}`)
  }
}
