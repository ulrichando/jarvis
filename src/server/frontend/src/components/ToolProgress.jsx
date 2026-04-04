import React, { useState, useEffect } from 'react';

/**
 * Displays a tool call with live progress and result.
 */
export default function ToolProgress({ execution }) {
  const [elapsed, setElapsed] = useState(0);

  useEffect(() => {
    if (execution.status === 'running') {
      const interval = setInterval(() => {
        setElapsed(Math.floor((Date.now() - execution.startTime) / 1000));
      }, 1000);
      return () => clearInterval(interval);
    }
  }, [execution.status, execution.startTime]);

  const getIcon = () => {
    if (execution.status === 'running') return '\u27F3';
    if (execution.status === 'error') return '\u2718';
    return '\u2714';
  };

  const getStatusColor = () => {
    if (execution.status === 'running') return '#3b82f6';
    if (execution.status === 'error') return '#ef4444';
    return '#22c55e';
  };

  const getLabel = () => {
    const name = execution.name;
    const args = execution.args || {};
    if (name === 'bash') return `Running: ${(args.command || '').slice(0, 80)}`;
    if (name === 'read_file') return `Reading ${args.path || ''}`;
    if (name === 'write_file') return `Writing ${args.path || ''}`;
    if (name === 'edit_file') return `Editing ${args.path || ''}`;
    if (name === 'search_files') return `Searching: ${args.pattern || ''}`;
    if (name === 'web_search') return `Web search: ${args.query || ''}`;
    if (name === 'web_fetch') return `Fetching: ${(args.url || '').slice(0, 60)}`;
    if (name === 'dispatch') return `Agent: ${args.agent_type || '?'} \u2014 ${(args.task || '').slice(0, 50)}`;
    if (name === 'think') return `Thinking...`;
    return `${name}`;
  };

  const [showResult, setShowResult] = useState(false);

  return (
    <div style={{
      padding: '8px 12px',
      margin: '4px 0',
      borderRadius: '6px',
      background: 'rgba(255,255,255,0.03)',
      borderLeft: `3px solid ${getStatusColor()}`,
      fontSize: '13px',
      fontFamily: 'monospace',
    }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
        <span style={{
          color: getStatusColor(),
          display: 'inline-block',
          animation: execution.status === 'running' ? 'tool-spin 1s linear infinite' : 'none',
        }}>
          {getIcon()}
        </span>
        <span style={{ color: '#e2e8f0', flex: 1, wordBreak: 'break-word' }}>{getLabel()}</span>
        <span style={{ color: '#64748b', fontSize: '11px', flexShrink: 0 }}>
          {execution.status === 'running' ? `${elapsed}s` : `${execution.elapsed || 0}s`}
        </span>
      </div>

      {execution.result && (
        <div style={{ marginTop: '4px' }}>
          <button
            onClick={() => setShowResult(!showResult)}
            style={{
              background: 'none', border: 'none', color: '#64748b',
              cursor: 'pointer', fontSize: '11px', padding: 0,
            }}
          >
            {showResult ? '\u25BE Hide result' : '\u25B8 Show result'}
          </button>
          {showResult && (
            <pre style={{
              marginTop: '4px', padding: '8px', borderRadius: '4px',
              background: 'rgba(0,0,0,0.3)', color: '#94a3b8',
              fontSize: '12px', maxHeight: '200px', overflow: 'auto',
              whiteSpace: 'pre-wrap', wordBreak: 'break-word',
            }}>
              {(execution.result || '').slice(0, 3000)}
              {(execution.result || '').length > 3000 ? '\n... (truncated)' : ''}
            </pre>
          )}
        </div>
      )}
    </div>
  );
}
