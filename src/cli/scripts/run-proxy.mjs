import { spawn } from 'node:child_process'
import { dirname, join } from 'node:path'
import process from 'node:process'
import { fileURLToPath } from 'node:url'

const scriptDir = dirname(fileURLToPath(import.meta.url))

const child = spawn(process.execPath, [join(scriptDir, 'bunw.mjs'), 'src/proxy/server.ts', ...process.argv.slice(2)], {
  stdio: 'inherit',
})

child.on('exit', code => {
  process.exit(code ?? 1)
})

child.on('error', error => {
  console.error(error.message)
  process.exit(1)
})
