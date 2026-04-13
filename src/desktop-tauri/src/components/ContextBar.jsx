import React from 'react';

/**
 * Shows token usage, context window fill, and cost in a compact bar.
 */
export default function ContextBar({ usage }) {
  if (!usage) return null;

  const total = (usage.input_tokens || 0) + (usage.output_tokens || 0);
  const cost = usage.session_cost || '';
  const ctxPct = usage.context_pct || 0;
  const ctxUsed = usage.context_used || 0;
  const ctxMax = usage.context_max || 0;

  const formatTokens = (n) => {
    if (n >= 1000000) return `${(n / 1000000).toFixed(1)}M`;
    if (n >= 1000) return `${(n / 1000).toFixed(1)}K`;
    return `${n}`;
  };

  // Context bar color: green < 60%, yellow 60-80%, red > 80%
  const barColor = ctxPct > 80 ? '#ef4444' : ctxPct > 60 ? '#f59e0b' : '#22c55e';

  return (
    <div style={{
      display: 'flex', gap: '12px', padding: '4px 12px', alignItems: 'center',
      fontSize: '11px', color: '#64748b', borderTop: '1px solid rgba(255,255,255,0.05)',
      fontFamily: 'monospace',
    }}>
      <span title="Input tokens">{'\u2191'} {formatTokens(usage.input_tokens || 0)}</span>
      <span title="Output tokens">{'\u2193'} {formatTokens(usage.output_tokens || 0)}</span>
      <span title="Total tokens">{'\u03A3'} {formatTokens(total)}</span>
      {ctxMax > 0 && (
        <span title={`Context: ${formatTokens(ctxUsed)} / ${formatTokens(ctxMax)} (${ctxPct}%)`}
              style={{ display: 'flex', alignItems: 'center', gap: '4px' }}>
          <span style={{
            width: '48px', height: '6px', background: 'rgba(255,255,255,0.08)',
            borderRadius: '3px', overflow: 'hidden', display: 'inline-block',
          }}>
            <span style={{
              width: `${Math.min(100, ctxPct)}%`, height: '100%',
              background: barColor, display: 'block', borderRadius: '3px',
              transition: 'width 0.3s ease, background 0.3s ease',
            }} />
          </span>
          <span style={{ color: barColor }}>{ctxPct}%</span>
        </span>
      )}
      {cost && <span style={{ marginLeft: 'auto' }}>{cost}</span>}
    </div>
  );
}
