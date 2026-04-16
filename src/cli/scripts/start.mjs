import { spawn } from 'node:child_process'
import { dirname, join } from 'node:path'
import process from 'node:process'
import { fileURLToPath } from 'node:url'

const scriptDir = dirname(fileURLToPath(import.meta.url))

const command =
  process.platform === 'win32' ? 'powershell' : 'bash'

const args =
  process.platform === 'win32'
    ? ['-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', join(scriptDir, 'start.ps1'), ...process.argv.slice(2)]
    : [join(scriptDir, 'start.sh'), ...process.argv.slice(2)]

const child = spawn(command, args, {
  stdio: 'inherit',
})

child.on('exit', code => {
  process.exit(code ?? 1)
})

child.on('error', error => {
  console.error(error.message)
  process.exit(1)
})
