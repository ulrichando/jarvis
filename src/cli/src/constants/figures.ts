import { env } from '../utils/env.js'

// The former is better vertically aligned, but isn't usually supported on Windows/Linux
export const BLACK_CIRCLE = env.platform === 'darwin' ? '⏺' : '●'
export const BULLET_OPERATOR = '∙'
export const TEARDROP_ASTERISK = '◉'
export const UP_ARROW = '↑' // up - used for opus 1m merge notice
export const DOWN_ARROW = '↓' // down - used for scroll hint
export const LIGHTNING_BOLT = '↯' // lightning - used for fast mode indicator
export const EFFORT_LOW = '○' // open circle - effort level: low
export const EFFORT_MEDIUM = '◐' // left-half-filled circle - effort level: medium
export const EFFORT_HIGH = '●' // filled circle - effort level: high
export const EFFORT_XHIGH = '◍' // circle with vertical fill - effort level: xhigh (between high and max)
export const EFFORT_MAX = '◉' // fisheye - effort level: max

// Media/trigger status indicators
export const PLAY_ICON = '▶' // play
export const PAUSE_ICON = '⏸' // pause

// MCP subscription indicators
export const REFRESH_ARROW = '↻' // resource-update indicator
export const CHANNEL_ARROW = '←' // inbound channel message indicator
export const INJECTED_ARROW = '→' // cross-session injected message indicator
export const FORK_GLYPH = '⑂' // fork directive indicator

// Review status indicators (ultrareview diamond states)
export const DIAMOND_OPEN = '◇' // running
export const DIAMOND_FILLED = '◆' // completed/failed
export const REFERENCE_MARK = '※' // komejirushi, away-summary recap marker

// Issue flag indicator
export const FLAG_ICON = '⚑' // issue flag banner

// Blockquote indicator
export const BLOCKQUOTE_BAR = '▎' // left one-quarter block, used as blockquote line prefix
export const HEAVY_HORIZONTAL = '━' // heavy box-drawing horizontal

// Bridge status indicators
export const BRIDGE_SPINNER_FRAMES = [
  '·|·',
  '·/·',
  '·—·',
  '·\\·',
]
export const BRIDGE_READY_INDICATOR = '·✔︎·'
export const BRIDGE_FAILED_INDICATOR = '×'
