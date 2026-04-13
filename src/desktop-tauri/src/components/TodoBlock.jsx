import React from 'react'

const STATUS_ICON = {
  completed:   { icon: '\u2714', color: '#22c55e' },
  in_progress: { icon: '\u27F3', color: '#3b82f6' },
  pending:     { icon: '\u00B7', color: '#475569'  },
  deleted:     { icon: '\u2715', color: '#6b7280'  },
}

/**
 * Renders a todo_write tool call as a styled checklist instead of raw text.
 * Receives the tool execution object (same shape as ToolProgress).
 */
export default function TodoBlock({ execution }) {
  const args = execution.args || {}
  const todos = args.todos || []
  if (!todos.length) return null

  const done   = todos.filter(t => t.status === 'completed').length
  const active = todos.filter(t => t.status === 'in_progress').length
  const total  = todos.length

  return (
    <div style={{
      padding: '8px 12px',
      margin: '3px 0',
      borderRadius: '5px',
      background: 'rgba(255,255,255,0.02)',
      borderLeft: '3px solid #6366f1',
      fontSize: '12px',
      fontFamily: 'monospace',
    }}>
      {/* Header */}
      <div style={{ display: 'flex', alignItems: 'center', gap: '8px', marginBottom: '6px' }}>
        <span style={{ color: '#6366f1' }}>&#9776;</span>
        <span style={{ color: '#94a3b8', flex: 1 }}>Tasks</span>
        <span style={{ color: '#475569', fontSize: '11px' }}>
          {done}/{total} done{active > 0 ? ` · ${active} running` : ''}
        </span>
      </div>

      {/* Task rows */}
      {todos.map((todo, i) => {
        const { icon, color } = STATUS_ICON[todo.status] || STATUS_ICON.pending
        const isDone = todo.status === 'completed'
        const isRunning = todo.status === 'in_progress'
        return (
          <div key={i} style={{
            display: 'flex', alignItems: 'flex-start', gap: '8px',
            padding: '2px 0',
            opacity: isDone ? 0.5 : 1,
          }}>
            <span style={{
              color,
              flexShrink: 0,
              animation: isRunning ? 'tool-spin 1s linear infinite' : 'none',
              display: 'inline-block',
            }}>
              {icon}
            </span>
            <span style={{
              color: isDone ? '#475569' : '#cbd5e1',
              textDecoration: isDone ? 'line-through' : 'none',
              flex: 1,
              lineHeight: '1.5',
            }}>
              {todo.content || todo.subject || todo.text || `Task ${i + 1}`}
            </span>
          </div>
        )
      })}
    </div>
  )
}
