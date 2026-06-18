import { readFileSync } from 'node:fs'
import { spawn } from 'node:child_process'
import { dirname, join, resolve } from 'node:path'
import process from 'node:process'
import { fileURLToPath } from 'node:url'

const scriptDir = dirname(fileURLToPath(import.meta.url))
const root = resolve(scriptDir, '..')

// Load an env file into process.env. First-set-wins semantics: keys already
// present in process.env are NOT overwritten, so the caller controls
// precedence by calling in lowest-priority-first order.
function loadEnvFile(filePath) {
  try {
    const content = readFileSync(filePath, 'utf8')
    for (const line of content.split('\n')) {
      const trimmed = line.trim()
      if (!trimmed || trimmed.startsWith('#')) continue
      const eqIdx = trimmed.indexOf('=')
      if (eqIdx === -1) continue
      const key = trimmed.slice(0, eqIdx).trim()
      const value = trimmed.slice(eqIdx + 1).trim()
      if (key && !(key in process.env)) {
        process.env[key] = value
      }
    }
  } catch {
    // file not found — skip
  }
}

// Like loadEnvFile but ALWAYS overwrites existing keys (last-wins). Used for
// ~/.jarvis/keys.env so rotated keys placed there take precedence over .env /
// .env.local without editing the repo's files. Mirrors start-desktop.sh's
// `set -a; source keys.env; set +a` sourcing order (last-source wins in bash).
function loadEnvFileOverride(filePath) {
  try {
    const content = readFileSync(filePath, 'utf8')
    for (const line of content.split('\n')) {
      const trimmed = line.trim()
      if (!trimmed || trimmed.startsWith('#')) continue
      const eqIdx = trimmed.indexOf('=')
      if (eqIdx === -1) continue
      const key = trimmed.slice(0, eqIdx).trim()
      const value = trimmed.slice(eqIdx + 1).trim()
      if (key) {
        process.env[key] = value
      }
    }
  } catch {
    // file not found — skip
  }
}

// First-set-wins semantics (see loadEnvFile above). Load .env.local
// FIRST so per-machine overrides take precedence, then fill any gaps
// from the repo-root .env (centralized LLM provider keys, consolidated
// 2026-05-15).
loadEnvFile(join(root, '.env.local'))
loadEnvFile(resolve(root, '..', '..', '.env'))
// Also load ~/.jarvis/keys.env (user-local secret store, gitignored).
// Uses override semantics so a rotated key here wins over .env / .env.local.
// Mirrors the start-desktop.sh and start.sh sourcing pattern.
const keysEnvPath = `${process.env.HOME ?? ''}/.jarvis/keys.env`
loadEnvFileOverride(keysEnvPath)

// Mark as development so native-install checks are skipped
if (!process.env.NODE_ENV) {
  process.env.NODE_ENV = 'development'
}

// Always disable Anthropic auth — jarvis-cli routes through its own proxy
process.env.JARVIS_DISABLE_AUTH = '1'

// Point the Anthropic SDK at the jarvis proxy instead of api.anthropic.com
const proxyPort = process.env.JARVIS_PROXY_PORT ?? '4000'
if (!process.env.ANTHROPIC_BASE_URL) {
  process.env.ANTHROPIC_BASE_URL = `http://localhost:${proxyPort}`
}

// Placeholder key so the Anthropic SDK doesn't reject missing-key config
if (!process.env.ANTHROPIC_API_KEY) {
  process.env.ANTHROPIC_API_KEY = 'jarvis-proxy'
}

// JARVIS proxy credential ("OAuth via login"): when `jarvis auth login` has
// provisioned a proxy token (written to ~/.jarvis/keys.env, sourced above),
// hand it to the Anthropic SDK as the auth token. The SDK sends it as
// `Authorization: Bearer <token>`, which the local proxy verifies — and it
// makes the /status screen show a real token instead of "none". Inert when
// unset, so fresh installs and pre-login sessions behave exactly as before.
if (!process.env.ANTHROPIC_AUTH_TOKEN && process.env.JARVIS_PROXY_TOKEN) {
  process.env.ANTHROPIC_AUTH_TOKEN = process.env.JARVIS_PROXY_TOKEN
}

// Disable tool deferral: non-Claude backends (Groq, DeepSeek) don't know the
// ToolSearch protocol and fail to load deferred tool schemas. Ship every tool
// schema up front so any model can call any tool first try.
if (!process.env.JARVIS_DISABLE_TOOL_DEFERRAL) {
  process.env.JARVIS_DISABLE_TOOL_DEFERRAL = '1'
}

// Check if the proxy is already running, start it if not
async function ensureProxy() {
  try {
    const res = await fetch(`http://localhost:${proxyPort}/health`, { signal: AbortSignal.timeout(500) })
    if (res.ok) return // already up
  } catch {
    // not running — start it
  }

  const proxy = spawn(process.execPath, [join(scriptDir, 'bunw.mjs'), 'src/proxy/server.ts'], {
    stdio: ['ignore', 'ignore', 'ignore'],
    detached: false,
    cwd: root,
    env: process.env,
  })

  proxy.on('error', err => {
    console.error('[jarvis] failed to start proxy:', err.message)
    process.exit(1)
  })

  // Wait up to 5s for the proxy to be ready
  const deadline = Date.now() + 5000
  while (Date.now() < deadline) {
    await new Promise(r => setTimeout(r, 150))
    try {
      const res = await fetch(`http://localhost:${proxyPort}/health`, { signal: AbortSignal.timeout(300) })
      if (res.ok) return
    } catch {
      // still starting
    }
  }
  console.error('[jarvis] proxy did not start in time')
  process.exit(1)
}

await ensureProxy()

const args = [
  join(scriptDir, 'bunw.mjs'),
  '--define',
  'MACRO.VERSION="2.1.107"',
  '--define',
  'MACRO.BUILD_TIME=""',
  '--define',
  'MACRO.PACKAGE_URL="@anthropic-ai/claude-code"',
  '--define',
  'MACRO.NATIVE_PACKAGE_URL="@anthropic-ai/claude-code-native"',
  '--define',
  'MACRO.ISSUES_EXPLAINER="report the issue at https://github.com/anthropics/claude-code/issues"',
  '--define',
  'MACRO.FEEDBACK_CHANNEL="https://github.com/anthropics/claude-code/issues"',
  '--define',
  'MACRO.VERSION_CHANGELOG=null',
  'src/entrypoints/cli.tsx',
  ...process.argv.slice(2),
]

const child = spawn(process.execPath, args, {
  stdio: 'inherit',
  cwd: root,
  env: process.env,
})

child.on('exit', code => {
  process.exit(code ?? 1)
})

child.on('error', error => {
  console.error(error.message)
  process.exit(1)
})
