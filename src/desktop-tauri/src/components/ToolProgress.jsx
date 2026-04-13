import React, { useState, useEffect } from 'react';

/**
 * Renders a single diff line with appropriate color.
 */
function DiffLine({ line }) {
  let color = '#94a3b8'  // default dim
  if (line.startsWith('+++') || line.startsWith('---')) color = '#64748b'
  else if (line.startsWith('+')) color = '#4ade80'   // green
  else if (line.startsWith('-')) color = '#f87171'   // red
  else if (line.startsWith('@@')) color = '#38bdf8'  // blue header

  return (
    <div style={{ color, whiteSpace: 'pre', fontFamily: 'monospace', fontSize: '11px', lineHeight: '1.5' }}>
      {line}
    </div>
  )
}

/**
 * Displays a tool call with live progress, elapsed time, optional diff, and expandable result.
 */
export default function ToolProgress({ execution }) {
  const [elapsed, setElapsed] = useState(0)
  const [showResult, setShowResult] = useState(false)
  const [showDiff, setShowDiff] = useState(true)  // diffs open by default

  useEffect(() => {
    if (execution.status === 'running') {
      const interval = setInterval(() => {
        setElapsed(Math.floor((Date.now() - execution.startTime) / 1000))
      }, 1000)
      return () => clearInterval(interval)
    }
  }, [execution.status, execution.startTime])

  const getIcon = () => {
    if (execution.status === 'running') return '\u27F3'
    if (execution.status === 'error') return '\u2718'
    return '\u2714'
  }

  const getStatusColor = () => {
    if (execution.status === 'running') return '#3b82f6'
    if (execution.status === 'error') return '#ef4444'
    return '#22c55e'
  }

  const getBadgeColor = () => {
    const name = execution.name
    if (name === 'bash') return '#f59e0b'
    if (name === 'read_file' || name === 'Grep' || name === 'search_files') return '#22d3ee'
    if (name === 'edit_file' || name === 'write_file') return '#fb923c'
    if (name === 'web_search' || name === 'web_fetch') return '#818cf8'
    if (name === 'dispatch') return '#c084fc'
    if (name === 'think') return '#475569'
    return '#64748b'
  }

  const getLabel = () => {
    const name = execution.name
    const args = execution.args || {}
    if (name === 'bash') return `$ ${(args.command || '').slice(0, 80)}`
    if (name === 'read_file') return `read  ${args.path || ''}`
    if (name === 'write_file') return `write ${args.path || ''}`
    if (name === 'edit_file') return `edit  ${args.path || ''}`
    if (name === 'search_files') return `search  ${args.pattern || ''}`
    if (name === 'Grep') return `grep  ${args.pattern || ''}`
    if (name === 'Glob') return `glob  ${args.pattern || ''}`
    if (name === 'web_search') return `web  ${args.query || ''}`
    if (name === 'web_fetch') return `fetch  ${(args.url || '').slice(0, 60)}`
    if (name === 'dispatch') return `agent:${args.agent_type || '?'}  ${(args.task || '').slice(0, 50)}`
    if (name === 'think') return `thinking...`
    return name
  }

  const fmtElapsed = (s) => s >= 60 ? `${Math.floor(s / 60)}m${s % 60}s` : `${s}s`
  const displayElapsed = execution.status === 'running' ? elapsed : (execution.elapsed || 0)

  const hasDiff = !!execution.diff
  const diffLines = hasDiff ? execution.diff.split('\n') : []

  return (
    <div style={{
      padding: '6px 10px',
      margin: '3px 0',
      borderRadius: '5px',
      background: 'rgba(255,255,255,0.02)',
      borderLeft: `3px solid ${getBadgeColor()}`,
      fontSize: '12px',
      fontFamily: 'monospace',
    }}>
      {/* Header row */}
      <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
        <span style={{
          color: getStatusColor(),
          display: 'inline-block',
          animation: execution.status === 'running' ? 'tool-spin 1s linear infinite' : 'none',
          flexShrink: 0,
        }}>
          {getIcon()}
        </span>
        <span style={{ color: '#cbd5e1', flex: 1, wordBreak: 'break-all' }}>{getLabel()}</span>
        <span style={{ color: '#475569', fontSize: '11px', flexShrink: 0 }}>
          {fmtElapsed(displayElapsed)}
        </span>
      </div>

      {/* Diff view — shown by default for edit/write, collapsible */}
      {hasDiff && execution.status !== 'running' && (
        <div style={{ marginTop: '5px' }}>
          <button
            onClick={() => setShowDiff(!showDiff)}
            style={{
              background: 'none', border: 'none',
              color: '#475569', cursor: 'pointer', fontSize: '11px', padding: 0,
            }}
          >
            {showDiff ? '▾ diff' : '▸ diff'}
            <span style={{ color: '#334155', marginLeft: '6px' }}>
              {diffLines.filter(l => l.startsWith('+')).length > 0 &&
                <span style={{ color: '#4ade80' }}>+{diffLines.filter(l => l.startsWith('+') && !l.startsWith('+++')).length}</span>}
              {' '}
              {diffLines.filter(l => l.startsWith('-')).length > 0 &&
                <span style={{ color: '#f87171' }}>-{diffLines.filter(l => l.startsWith('-') && !l.startsWith('---')).length}</span>}
            </span>
          </button>
          {showDiff && (
            <div style={{
              marginTop: '4px', padding: '6px 8px', borderRadius: '4px',
              background: 'rgba(0,0,0,0.35)', maxHeight: '240px', overflow: 'auto',
            }}>
              {diffLines.map((line, i) => <DiffLine key={i} line={line} />)}
            </div>
          )}
        </div>
      )}

      {/* Raw result toggle — only show when no diff, or as secondary detail */}
      {execution.result && !hasDiff && (
        <div style={{ marginTop: '4px' }}>
          <button
            onClick={() => setShowResult(!showResult)}
            style={{
              background: 'none', border: 'none', color: '#475569',
              cursor: 'pointer', fontSize: '11px', padding: 0,
            }}
          >
            {showResult ? '▾ output' : '▸ output'}
          </button>
          {showResult && (
            <pre style={{
              marginTop: '4px', padding: '6px 8px', borderRadius: '4px',
              background: 'rgba(0,0,0,0.3)', color: '#94a3b8',
              fontSize: '11px', maxHeight: '180px', overflow: 'auto',
              whiteSpace: 'pre-wrap', wordBreak: 'break-word',
            }}>
              {(execution.result || '').slice(0, 3000)}
              {(execution.result || '').length > 3000 ? '\n... (truncated)' : ''}
            </pre>
          )}
        </div>
      )}
    </div>
  )
}
