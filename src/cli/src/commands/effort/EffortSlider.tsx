import * as React from 'react'
import { useMainLoopModel } from '../../hooks/useMainLoopModel.js'
import { Box, Text, useInput } from '../../ink.js'
import { useAppState, useSetAppState } from '../../state/AppState.js'
import type { LocalJSXCommandOnDone } from '../../types/command.js'
import {
  EFFORT_LEVELS,
  getDisplayedEffortLevel,
  getEffortLevelDescription,
  modelSupportsEffort,
  modelSupportsMaxEffort,
} from '../../utils/effort.js'
import { effortLevelToSymbol } from '../../components/EffortIndicator.js'
import { executeEffort, showCurrentEffort } from './effort.js'

// Interactive ←/→ slider for /effort with no args — parity with Claude Code's
// effort selector (low · medium · high · xhigh · max, Faster → Smarter, Enter
// to confirm, Esc to cancel). JARVIS already supported every level via
// `/effort <level>`; this makes them discoverable and adjustable visually.
// Research: code.claude.com/docs/en/model-config + the effort-slider write-ups.

// Column width per level (fits the longest label "medium"/"xhigh" + padding);
// the rail marker and the labels share this grid so they stay aligned.
const CELL = 9

function center(s: string, width: number): string {
  if (s.length >= width) return s.slice(0, width)
  const pad = width - s.length
  const left = Math.floor(pad / 2)
  return ' '.repeat(left) + s + ' '.repeat(pad - left)
}

export function EffortSlider({
  onDone,
}: {
  onDone: LocalJSXCommandOnDone
}): React.ReactNode {
  const effortValue = useAppState((s) => s.effortValue)
  const setAppState = useSetAppState()
  const model = useMainLoopModel()
  const supported = modelSupportsEffort(model)
  const maxSupported = modelSupportsMaxEffort(model)
  const startLevel = getDisplayedEffortLevel(model, effortValue)
  const [index, setIndex] = React.useState(() =>
    Math.max(0, EFFORT_LEVELS.indexOf(startLevel)),
  )
  // Enter (below) reads the selected level from useInput's handler closure,
  // which useEventCallback only refreshes via a useLayoutEffect — i.e. after
  // commit. Under Ink's event dispatch that closure can trail the visually
  // selected index by a step, so the applied level (also sent to the API and
  // shown in the status bar) lagged the marker by one, needing 2-3 changes to
  // catch up. A ref assigned during render always mirrors what the slider
  // shows, so the apply matches the marker on the first confirm.
  const indexRef = React.useRef(index)
  indexRef.current = index

  // Models that don't support effort at all: fall back to the text status
  // (which renders the "not supported / use X" message) instead of a slider.
  React.useEffect(() => {
    if (!supported) onDone(showCurrentEffort(effortValue, model).message)
  }, [supported, effortValue, model, onDone])

  useInput((_input, key) => {
    if (!supported) return
    if (key.leftArrow) {
      setIndex((i: number) => Math.max(0, i - 1))
    } else if (key.rightArrow) {
      setIndex((i: number) => Math.min(EFFORT_LEVELS.length - 1, i + 1))
    } else if (key.return) {
      const result = executeEffort(EFFORT_LEVELS[indexRef.current])
      if (result.effortUpdate) {
        const value = result.effortUpdate.value
        setAppState((prev) => ({ ...prev, effortValue: value }))
      }
      onDone(result.message)
    } else if (key.escape) {
      onDone(`Effort unchanged (${startLevel})`)
    }
  })

  if (!supported) return null

  const selected = EFFORT_LEVELS[index]
  const width = CELL * EFFORT_LEVELS.length
  const markerCol = index * CELL + Math.floor(CELL / 2)
  const rail = '─'.repeat(width)

  return (
    // alignItems="center" centers the whole widget as one unit. Every row must
    // be a <Box> (not a bare <Text>): a bare Text flex-child expands to the full
    // width and renders left-aligned, which is what split the "Effort" header and
    // the help line off to the left edge while the Box rows (rail/labels) centered.
    <Box flexDirection="column" marginY={1} alignItems="center">
      <Box>
        <Text bold>Effort</Text>
      </Box>
      <Box width={width} justifyContent="space-between">
        <Text dimColor>Faster</Text>
        <Text dimColor>Smarter</Text>
      </Box>
      {/* Rail with a single coloured ▲ marking the selected level. */}
      <Box>
        <Text dimColor>{rail.slice(0, markerCol)}</Text>
        <Text color="cyan" bold>
          ▲
        </Text>
        <Text dimColor>{rail.slice(markerCol + 1)}</Text>
      </Box>
      <Box>
        {EFFORT_LEVELS.map((lvl, i) => {
          const isMaxUnavailable = lvl === 'max' && !maxSupported
          return (
            <Text
              key={lvl}
              bold={i === index}
              color={i === index ? 'cyan' : undefined}
              dimColor={isMaxUnavailable || i !== index}
            >
              {center(lvl, CELL)}
            </Text>
          )
        })}
      </Box>
      <Box marginTop={1}>
        <Text>
          {effortLevelToSymbol(selected)} {selected}
          {selected === 'max' && !maxSupported
            ? ' (not supported by this model — applies after you switch)'
            : ''}{' '}
          — {getEffortLevelDescription(selected)}
        </Text>
      </Box>
      <Box>
        <Text dimColor>←/→ to adjust · Enter to confirm · Esc to cancel</Text>
      </Box>
    </Box>
  )
}
