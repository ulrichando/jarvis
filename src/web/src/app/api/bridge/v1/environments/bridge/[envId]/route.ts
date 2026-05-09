import { getStore } from '@/lib/bridge/db'
import {
  deleteEnvironment,
  findEnvironment,
  validateEnvSecret,
} from '@/lib/bridge/store'
import { extractBearer } from '@/lib/bridge/auth'
import { bridgeError } from '@/lib/bridge/errors'

export async function DELETE(
  req: Request,
  ctx: { params: Promise<{ envId: string }> },
): Promise<Response> {
  const { envId } = await ctx.params
  const token = extractBearer(req.headers.get('authorization'))
  if (!token) {
    return bridgeError(401, 'unauthorized', 'Missing bearer token')
  }
  const store = getStore()
  if (!findEnvironment(store, envId)) {
    return bridgeError(404, 'not_found', 'Environment not found')
  }
  if (!validateEnvSecret(store, envId, token)) {
    return bridgeError(401, 'unauthorized', 'Invalid environment_secret')
  }
  deleteEnvironment(store, envId)
  return new Response(null, { status: 204 })
}
