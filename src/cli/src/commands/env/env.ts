import { release } from 'node:os'
import type { LocalCommandCall } from '../../types/command.js'
import { env as host } from '../../utils/env.js'

// The real /env source was gitignored out of the public build (see env/index.ts
// history). This is a minimal, self-contained readout of the same environment
// facts JARVIS surfaces elsewhere (utils/env.ts), so /env is useful rather than
// a dead "source is missing" stub. NB: env.isSSH / isRunningWithBun /
// detectDeploymentEnvironment are FUNCTIONS on the env object — call them.
export const call: LocalCommandCall = async () => {
  const runtime = host.isRunningWithBun()
    ? `Bun ${process.versions.bun ?? ''}`.trim()
    : `Node ${host.nodeVersion}`
  const value = [
    `Jarvis CLI    ${MACRO.BUILD_TIME ? `${MACRO.VERSION} (built ${MACRO.BUILD_TIME})` : MACRO.VERSION}`,
    `Platform      ${host.platform} · ${host.arch} · kernel ${release()}`,
    `Runtime       ${runtime}`,
    `Terminal      ${host.terminal ?? 'unknown'}${host.isSSH() ? ' (ssh)' : ''}`,
    `Deployment    ${host.detectDeploymentEnvironment()}`,
    `Working dir   ${process.cwd()}`,
  ].join('\n')
  return { type: 'text', value }
}
