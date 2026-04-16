import { accessSync, constants } from 'node:fs'
import { spawn } from 'node:child_process'
import { dirname, delimiter, join, resolve } from 'node:path'
import process from 'node:process'
import { fileURLToPath } from 'node:url'

const scriptDir = dirname(fileURLToPath(import.meta.url))
const root = resolve(scriptDir, '..')

function isExecutable(filePath) {
  if (!filePath) return false
  try {
    accessSync(filePath, constants.X_OK)
    return true
  } catch {
    return false
  }
}

function normalizeOs() {
  switch (process.platform) {
    case 'linux':
      return 'linux'
    case 'darwin':
      return 'darwin'
    case 'win32':
      return 'windows'
    default:
      return null
  }
}

function normalizeArch() {
  switch (process.arch) {
    case 'x64':
      return 'x64'
    case 'arm64':
      return 'arm64'
    default:
      return null
  }
}

function resolveFromPath(binaryName) {
  const pathValue = process.env.PATH ?? ''
  for (const entry of pathValue.split(delimiter)) {
    if (!entry) continue
    const candidate = join(entry, binaryName)
    if (isExecutable(candidate)) {
      return candidate
    }
  }
  return null
}

function resolveBun() {
  const ext = process.platform === 'win32' ? '.exe' : ''
  const binaryName = `bun${ext}`
  const os = normalizeOs()
  const arch = normalizeArch()
  const candidates = []

  if (process.env.BUN_BIN) {
    candidates.push(process.env.BUN_BIN)
  }

  if (os && arch) {
    candidates.push(join(root, 'vendor', 'bun', `${os}-${arch}`, binaryName))
  }

  candidates.push(
    join(root, 'vendor', 'bun', 'bin', binaryName),
    join(root, 'tools', 'bun', 'bin', binaryName),
    join(root, '.bun', 'bin', binaryName),
  )

  if (process.env.HOME) {
    candidates.push(join(process.env.HOME, '.bun', 'bin', binaryName))
  }

  if (process.env.USERPROFILE) {
    candidates.push(join(process.env.USERPROFILE, '.bun', 'bin', binaryName))
  }

  if (process.platform === 'win32') {
    candidates.push(
      'C:\\Program Files\\Bun\\bun.exe',
      'C:\\Program Files (x86)\\Bun\\bun.exe',
    )
  } else {
    candidates.push('/usr/local/bin/bun', '/opt/homebrew/bin/bun', '/usr/bin/bun')
  }

  for (const candidate of candidates) {
    if (isExecutable(candidate)) {
      return candidate
    }
  }

  return resolveFromPath(binaryName)
}

const bunPath = resolveBun()
if (!bunPath) {
  console.error('Error: Bun was not found. Provide BUN_BIN, add bun to PATH, or bundle vendor/bun/<os>-<arch>/bun.')
  process.exit(1)
}

const child = spawn(bunPath, process.argv.slice(2), {
  stdio: 'inherit',
})

child.on('exit', code => {
  process.exit(code ?? 1)
})

child.on('error', error => {
  console.error(error.message)
  process.exit(1)
})
