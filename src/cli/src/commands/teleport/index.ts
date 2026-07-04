import type { Command } from '../../commands.js'

// `/teleport [id]` (alias `/tp`) — pull a cloud /code session to this machine
// from inside a session (the in-REPL counterpart of the `jarvis teleport`
// shell subcommand; self-hosted equivalent of claude.ai's /teleport). It opens
// an interactive arrow-key picker of your cloud sessions; `/teleport <id>`
// pulls that one directly. The UI + logic live in TeleportPicker.tsx + core.ts.
//
// Must be registered in the MAIN command list (see commands.ts), NOT in
// INTERNAL_ONLY_COMMANDS — that array is stripped from external/JARVIS builds,
// which is why the command never surfaced when the stub lived there.
const teleport = {
  type: 'local-jsx',
  name: 'teleport',
  aliases: ['tp'],
  description: 'Pull a cloud /code session to this machine (picker, or /teleport <id>)',
  isEnabled: () => true,
  isHidden: false,
  load: () => import('./TeleportPicker.js'),
} satisfies Command

export default teleport
