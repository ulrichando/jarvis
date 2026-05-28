import React, { useRef, useEffect } from 'react'

// Recent chat lines, scroll-pinned to bottom.
// Reads the same wsMessages stream that the existing ChatPanel consumes,
// filtering for chat_message (user typed) + chat_response (assistant) +
// any user_message echoes from the bridge.
const MAX_LINES = 12

function pickLines(wsMessages) {
  const out = []
  for (const m of wsMessages) {
    if (m.type === 'chat_response' && typeof m.text === 'string') {
      out.push({ who: 'jarvis', text: m.text })
    } else if (m.type === 'user_message' && typeof m.text === 'string') {
      out.push({ who: 'user', text: m.text })
    } else if (m.type === 'query' && typeof m.text === 'string') {
      out.push({ who: 'user', text: m.text })
    }
  }
  return out.slice(-MAX_LINES)
}

export default function KioskTranscript({ wsMessages }) {
  const ref = useRef(null)
  const lines = pickLines(wsMessages || [])

  useEffect(() => {
    if (ref.current) ref.current.scrollTop = ref.current.scrollHeight
  }, [lines.length])

  return (
    <div className="kiosk-transcript" ref={ref}>
      {lines.map((l, i) => (
        <div key={i} className={`kiosk-line kiosk-line-${l.who}`}>
          {l.who === 'user' ? '>' : ''} {l.text}
        </div>
      ))}
      <style>{`
        .kiosk-transcript {
          overflow-y: auto;
          padding: 24px 64px;
          color: rgba(255,255,255,0.85);
          font: 18px/1.55 ui-monospace, monospace;
        }
        .kiosk-line { margin: 8px 0; white-space: pre-wrap; word-wrap: break-word; }
        .kiosk-line-user   { color: rgba(255,255,255,0.95); }
        .kiosk-line-jarvis { color: rgba(255,255,255,0.70); }
      `}</style>
    </div>
  )
}
