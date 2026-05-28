import React from 'react'

// 8 fixed bars, CSS-keyframe animation. Two states:
//   - active=true  → bars animate (scaleY oscillation, staggered)
//   - active=false → bars rest at a small static height
// Per the regression-prevention rule from CLAUDE.md: NO per-frame React
// state in voice UI. This component renders once when `active` changes.
//
// The animation is entirely in CSS keyframes; the `active` prop only
// flips a className.
const BARS = 8

export default function KioskVoiceWaveform({ active }) {
  return (
    <div className={`kiosk-wave ${active ? 'kiosk-wave-active' : ''}`}>
      {Array.from({ length: BARS }).map((_, i) => (
        <span
          key={i}
          className="kiosk-wave-bar"
          style={{ animationDelay: `${i * 80}ms` }}
        />
      ))}
      <style>{`
        .kiosk-wave {
          display: flex;
          align-items: center;
          justify-content: center;
          gap: 4px;
          height: 40px;
        }
        .kiosk-wave-bar {
          display: inline-block;
          width: 3px;
          height: 6px;
          background: rgba(255,255,255,0.55);
          border-radius: 2px;
          transform-origin: center;
        }
        .kiosk-wave-active .kiosk-wave-bar {
          animation: kiosk-wave-osc 700ms ease-in-out infinite;
        }
        @keyframes kiosk-wave-osc {
          0%, 100% { transform: scaleY(1);   opacity: 0.55; }
          50%      { transform: scaleY(5.5); opacity: 1; }
        }
      `}</style>
    </div>
  )
}
