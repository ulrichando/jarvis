import {
  EFFORT_HIGH,
  EFFORT_LOW,
  EFFORT_MAX,
  EFFORT_MEDIUM,
  EFFORT_XHIGH,
} from '../constants/figures.js'
import {
  type EffortLevel,
  type EffortValue,
  getDisplayedEffortLevel,
} from '../utils/effort.js'

/**
 * Build the text for the effort notification, e.g. "◐ medium · /effort".
 * Does NOT gate on modelSupportsEffort — the notification serves as persistent
 * user feedback about the effort setting and is actionable (points to /effort).
 * getDisplayedEffortLevel handles unsupported models by returning a sensible
 * default ('high').
 */
export function getEffortNotificationText(
  effortValue: EffortValue | undefined,
  model: string,
): string {
  const level = getDisplayedEffortLevel(model, effortValue)
  return `${effortLevelToSymbol(level)} ${level} · /effort`
}

export function effortLevelToSymbol(level: EffortLevel): string {
  switch (level) {
    case 'low':
      return EFFORT_LOW
    case 'medium':
      return EFFORT_MEDIUM
    case 'high':
      return EFFORT_HIGH
    case 'xhigh':
      return EFFORT_XHIGH
    case 'max':
      return EFFORT_MAX
    default:
      // Defensive: level can originate from remote config. If an unknown
      // value slips through, render the high symbol rather than undefined.
      return EFFORT_HIGH
  }
}
