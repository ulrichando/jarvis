import * as path from 'path'
import { setCwd } from '../../utils/Shell.js'
import { setOriginalCwd } from '../../bootstrap/state.js'
import { findGitRoot } from '../../utils/git.js'
import { clearRepositoryCaches } from '../../utils/detectRepository.js'
import { resetGitFileWatcher } from '../../utils/git/gitFilesystem.js'
import {
  getKnownPathsForRepo,
  filterExistingPaths,
  validateRepoAtPath,
  updateGithubRepoPathMapping,
} from '../../utils/githubRepoPathMapping.js'
import type { CodeSession } from '../../utils/teleport/api.js'

export type RepoSwitchResult =
  | { kind: 'no_repo' } // session carries no repo requirement
  | { kind: 'already_here' } // current dir is already the session's repo
  | { kind: 'switched'; path: string }
  | { kind: 'not_found'; repo: string }

// Collaborators, injectable so the decision logic is testable without touching
// the real filesystem / process cwd.
export type RepoSwitchDeps = {
  cwd: () => string
  validateRepoAtPath: (p: string, repo: string) => Promise<boolean>
  /** Tracked checkout paths for the repo, already filtered to existing dirs. */
  trackedPaths: (repo: string) => Promise<string[]>
  /** The parent dir of the current git root — where sibling checkouts live. */
  gitRootParent: () => string
  chdir: (p: string) => void
}

const realDeps: RepoSwitchDeps = {
  cwd: () => process.cwd(),
  validateRepoAtPath,
  trackedPaths: async repo => filterExistingPaths(getKnownPathsForRepo(repo)),
  gitRootParent: () => {
    const cwd = process.cwd()
    return path.dirname(findGitRoot(cwd) ?? cwd)
  },
  chdir: p => {
    process.chdir(p)
    setCwd(p)
    setOriginalCwd(p)
    // The repo-detection caches (repositoryWithHostCache + the gitWatcher
    // remote-url/branch cache) are cwd-independent and were already warmed with
    // the OLD repo when the picker rendered. Without busting them the resume's
    // validateSessionRepository still reads the old repo → false "not_in_repo".
    // (The --teleport flag never hits this: it chdirs at startup before anything
    // caches.) ponytail: reset is the blunt-but-correct tool for a mid-session
    // repo change, which normal operation never does.
    clearRepositoryCaches()
    resetGitFileWatcher()
    // Seed tracked-paths config so the next teleport into this repo is instant.
    void updateGithubRepoPathMapping()
  },
}

// When a teleported session belongs to a repo we're not currently in, find a
// local checkout of that repo and hop into it — mirroring what `jarvis
// --teleport` does at startup (process.chdir + setCwd + setOriginalCwd) — so
// the in-place resume validates as a repo match instead of dead-ending.
//
// Discovery order: tracked checkout paths (config, populated when jarvis has
// been launched there), then a sibling of the current git root named after the
// repo (the common `~/Projects/<jarvis>` + `~/Projects/<repo>` layout). Every
// candidate is remote-verified before we chdir.
export async function switchToRepoCheckout(
  session: CodeSession,
  deps: RepoSwitchDeps = realDeps,
): Promise<RepoSwitchResult> {
  const repo = session.repo
  if (!repo) return { kind: 'no_repo' }
  const repoKey = `${repo.owner.login}/${repo.name}`

  if (await deps.validateRepoAtPath(deps.cwd(), repoKey)) {
    return { kind: 'already_here' }
  }

  const candidates = new Set<string>(await deps.trackedPaths(repoKey))
  candidates.add(path.join(deps.gitRootParent(), repo.name))

  for (const candidate of candidates) {
    if (await deps.validateRepoAtPath(candidate, repoKey)) {
      deps.chdir(candidate)
      return { kind: 'switched', path: candidate }
    }
  }
  return { kind: 'not_found', repo: repoKey }
}
