import React from 'react';

/**
 * Shows token usage and cost in a compact bar.
 */
export default function ContextBar({ usage }) {
  if (!usage) return null;

  const total = (usage.input_tokens || 0) + (usage.output_tokens || 0);
  const cost = usage.session_cost || '';

  const formatTokens = (n) => {
    if (n >= 1000000) return `${(n / 1000000).toFixed(1)}M`;
    if (n >= 1000) return `${(n / 1000).toFixed(1)}K`;
    return `${n}`;
  };

  return (
    <div style={{
      display: 'flex', gap: '12px', padding: '4px 12px',
      fontSize: '11px', color: '#64748b', borderTop: '1px solid rgba(255,255,255,0.05)',
      fontFamily: 'monospace',
    }}>
      <span title="Input tokens">{'\u2191'} {formatTokens(usage.input_tokens || 0)}</span>
      <span title="Output tokens">{'\u2193'} {formatTokens(usage.output_tokens || 0)}</span>
      <span title="Total tokens">{'\u03A3'} {formatTokens(total)}</span>
      {cost && <span style={{ marginLeft: 'auto' }}>{cost}</span>}
    </div>
  );
}
