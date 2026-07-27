import type { SDKMessage } from 'src/entrypoints/agentSdkTypes.js'
import { checkGate_CACHED_OR_BLOCKING } from '../../../services/analytics/growthbook.js'
import { isPolicyAllowed } from '../../../services/policyLimits/index.js'
import { detectCurrentRepositoryWithHost } from '../../detectRepository.js'
import { isEnvTruthy } from '../../envUtils.js'
import type { TodoList } from '../../todo/types.js'
import {
  checkGithubAppInstalled,
  checkHasRemoteEnvironment,
  checkIsInGitRepo,
  checkNeedsClaudeAiLogin,
} from './preconditions.js'

/**
 * Background remote session type for managing teleport sessions
 */
export type BackgroundRemoteSession = {
  id: string
  command: string
  startTime: number
  status: 'starting' | 'running' | 'completed' | 'failed' | 'killed'
  todoList: TodoList
  title: string
  type: 'remote_session'
  log: SDKMessage[]
}

/**
 * Precondition failures for background remote sessions
 */
export type BackgroundRemoteSessionPrecondition =
  | { type: 'not_logged_in' }
  | { type: 'no_remote_environment' }
  | { type: 'not_in_git_repo' }
  | { type: 'no_git_remote' }
  | { type: 'github_app_not_installed' }
  | { type: 'policy_blocked' }

/**
 * Checks eligibility for creating a background remote session
 * Returns an array of failed preconditions (empty array means all checks passed)
 *
 * @returns Array of failed preconditions
 */
export async function checkBackgroundRemoteSessionEligibility({
  skipBundle = false,
}: {
  skipBundle?: boolean
} = {}): Promise<BackgroundRemoteSessionPrecondition[]> {
  const errors: BackgroundRemoteSessionPrecondition[] = []

  // Check policy first - if blocked, no need to check other preconditions
  if (!isPolicyAllowed('allow_remote_sessions')) {
    errors.push({ type: 'policy_blocked' })
    return errors
  }

  // JARVIS mode (JARVIS_CCR_BASE_URL): the self-hosted jarvis-web CCR backend
  // runs each session in a container. With a git repo it clones via the git
  // source (teleport.tsx); with JARVIS_CCR_ENABLE_BUNDLE=1 + a local-only repo
  // it seeds from an uploaded git bundle (teleport.tsx bundleSeedGateOn);
  // WITHOUT either it runs scratch (ultraplan / research / remote-control
  // tasks that need no codebase). So none of the Anthropic-cloud preconditions
  // apply (claude.ai login, Anthropic env API, Claude GitHub app) and a git
  // repo is NOT required — eligibility here greenlights a SESSION, not a
  // bundle; when the bundle env is off, teleport just falls back to git-source
  // or scratch. Only the policy gate above stands. (Requiring a git repo
  // previously blocked repo-less tasks launched from a non-git dir like /tmp
  // — 2026-07-09.)
  if (process.env.JARVIS_CCR_BASE_URL) {
    return errors
  }

  const [needsLogin, hasRemoteEnv, repository] = await Promise.all([
    checkNeedsClaudeAiLogin(),
    checkHasRemoteEnvironment(),
    detectCurrentRepositoryWithHost(),
  ])

  if (needsLogin) {
    errors.push({ type: 'not_logged_in' })
  }

  if (!hasRemoteEnv) {
    errors.push({ type: 'no_remote_environment' })
  }

  // When bundle seeding is on, in-git-repo is enough — CCR can seed from
  // a local bundle. No GitHub remote or app needed. Same gate STRUCTURE as
  // teleport.tsx bundleSeedGateOn (jarvis mode gates purely on
  // JARVIS_CCR_ENABLE_BUNDLE; stock mode keeps the upstream disjunction and
  // JARVIS_CCR_ENABLE_BUNDLE has NO effect there — never bundle a private
  // repo to Anthropic's cloud from a stock config). The JARVIS-mode early
  // return above means the jarvis arm only matters if that return ever
  // changes, but the eligibility precondition and the actual teleport
  // attempt must agree.
  const inJarvis = !!process.env.JARVIS_CCR_BASE_URL
  const bundleSeedGateOn =
    !skipBundle &&
    (isEnvTruthy(process.env.CCR_FORCE_BUNDLE) ||
      (inJarvis
        ? isEnvTruthy(process.env.JARVIS_CCR_ENABLE_BUNDLE)
        : isEnvTruthy(process.env.CCR_ENABLE_BUNDLE) ||
          (await checkGate_CACHED_OR_BLOCKING('tengu_ccr_bundle_seed_enabled'))))

  if (!checkIsInGitRepo()) {
    errors.push({ type: 'not_in_git_repo' })
  } else if (bundleSeedGateOn) {
    // has .git/, bundle will work — skip remote+app checks
  } else if (repository === null) {
    errors.push({ type: 'no_git_remote' })
  } else if (repository.host === 'github.com') {
    const hasGithubApp = await checkGithubAppInstalled(
      repository.owner,
      repository.name,
    )
    if (!hasGithubApp) {
      errors.push({ type: 'github_app_not_installed' })
    }
  }

  return errors
}
