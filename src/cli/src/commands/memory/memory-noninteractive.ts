import { getRelativeMemoryPath } from '../../components/memory/MemoryUpdateNotification.js'
import type { LocalCommandCall } from '../../types/command.js'
import { getMemoryFiles, type MemoryFileInfo } from '../../utils/claudemd.js'

/**
 * Text `/memory` for non-interactive sessions. The interactive `memory.tsx`
 * opens a file editor, which a --print / SDK / Remote Control / `/code`
 * container session can't do — so this lists the active memory files instead
 * (editing happens in your own editor). Without it `/memory` errored with
 * "Unknown skill: memory".
 */
export const call: LocalCommandCall = async () => {
  let files: MemoryFileInfo[] = []
  try {
    files = await getMemoryFiles()
  } catch {
    return { type: 'text', value: 'No memory files found.' }
  }
  if (!files || files.length === 0) {
    return { type: 'text', value: 'No memory files found.' }
  }
  const lines = files.map((f: MemoryFileInfo) => {
    let rel = f.path
    try {
      rel = getRelativeMemoryPath(f.path)
    } catch {
      /* fall back to the absolute path */
    }
    return `• ${rel} (${f.type})`
  })
  return {
    type: 'text',
    value: `Memory files (${files.length}) — edit these in your editor:\n${lines.join('\n')}`,
  }
}
