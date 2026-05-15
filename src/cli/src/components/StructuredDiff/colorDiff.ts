import { createRequire } from 'module'
import { isEnvDefinedFalsy } from '../../utils/envUtils.js'

const require = createRequire(import.meta.url)

type ColorDiffModule = {
  ColorDiff: unknown
  ColorFile: unknown
  getSyntaxTheme: (themeName: string) => unknown
}

let nativeModule: ColorDiffModule | null = null
let loadFailed = false
try {
  nativeModule = require('color-diff-napi') as ColorDiffModule
} catch {
  loadFailed = true
}

export type SyntaxTheme = unknown
export type ColorModuleUnavailableReason = 'env' | 'missing'

/**
 * Returns a static reason why the color-diff module is unavailable, or null if available.
 * 'env'     = disabled via CLAUDE_CODE_SYNTAX_HIGHLIGHT
 * 'missing' = the color-diff-napi package is not installed
 */
export function getColorModuleUnavailableReason(): ColorModuleUnavailableReason | null {
  if (isEnvDefinedFalsy(process.env.CLAUDE_CODE_SYNTAX_HIGHLIGHT)) {
    return 'env'
  }
  if (loadFailed || nativeModule === null) {
    return 'missing'
  }
  return null
}

export function expectColorDiff(): unknown {
  return getColorModuleUnavailableReason() === null ? nativeModule!.ColorDiff : null
}

export function expectColorFile(): unknown {
  return getColorModuleUnavailableReason() === null ? nativeModule!.ColorFile : null
}

export function getSyntaxTheme(themeName: string): SyntaxTheme | null {
  return getColorModuleUnavailableReason() === null
    ? nativeModule!.getSyntaxTheme(themeName)
    : null
}
