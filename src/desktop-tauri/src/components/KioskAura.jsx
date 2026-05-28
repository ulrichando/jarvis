import React from 'react'

// KioskAura — a pulsing-energy-field visualizer.
//
// Original implementation, pure SVG + CSS keyframes — no Three.js, no
// shader, no per-frame React. Honors the project's "no per-frame React
// state in voice UI" rule from CLAUDE.md.
//
// Visual layers, outside-in:
//   1. Soft outer halo (large radial gradient, gentle breathing)
//   2. Three expanding ripple rings, staggered (the "pulse" of the field)
//   3. Two orbital tracks of small particles (rotating opposite directions)
//   4. Bright inner core (radial gradient circle with state-driven glow)
//
// State (one of "offline" / "idle" / "listening" / "speaking" / "thinking")
// only swaps the active CSS class. All motion is CSS keyframes; the
// component re-renders only when the state prop changes.
const COLOR = '#1FD5F9'

export default function KioskAura({ state = 'idle', size = 340 }) {
  const cls = `kiosk-aura kiosk-aura--${state}`
  return (
    <div className={cls} style={{ width: size, height: size }}>
      <svg viewBox="-100 -100 200 200" className="kiosk-aura-svg" xmlns="http://www.w3.org/2000/svg">
        <defs>
          <radialGradient id="aura-core-grad" cx="0.5" cy="0.5" r="0.5">
            <stop offset="0%"   stopColor={COLOR} stopOpacity="1" />
            <stop offset="50%"  stopColor={COLOR} stopOpacity="0.65" />
            <stop offset="100%" stopColor={COLOR} stopOpacity="0" />
          </radialGradient>
          <radialGradient id="aura-halo-grad" cx="0.5" cy="0.5" r="0.5">
            <stop offset="0%"   stopColor={COLOR} stopOpacity="0.30" />
            <stop offset="70%"  stopColor={COLOR} stopOpacity="0.05" />
            <stop offset="100%" stopColor={COLOR} stopOpacity="0" />
          </radialGradient>
        </defs>

        {/* Outer halo — slow breathing background glow */}
        <circle cx="0" cy="0" r="90" fill="url(#aura-halo-grad)" className="aura-halo" />

        {/* Three ripple rings — staggered expansion */}
        <circle cx="0" cy="0" r="30" fill="none" stroke={COLOR} strokeWidth="1.2"
                className="aura-ripple aura-ripple-1" />
        <circle cx="0" cy="0" r="30" fill="none" stroke={COLOR} strokeWidth="1.2"
                className="aura-ripple aura-ripple-2" />
        <circle cx="0" cy="0" r="30" fill="none" stroke={COLOR} strokeWidth="1.2"
                className="aura-ripple aura-ripple-3" />

        {/* Outer orbital track — 6 particles */}
        <g className="aura-orbit-outer">
          {[0, 60, 120, 180, 240, 300].map((deg, i) => {
            const r = 55
            const x = r * Math.cos((deg * Math.PI) / 180)
            const y = r * Math.sin((deg * Math.PI) / 180)
            return <circle key={i} cx={x} cy={y} r="1.4" fill={COLOR} opacity="0.8" />
          })}
        </g>

        {/* Inner orbital track — 4 particles, rotating opposite */}
        <g className="aura-orbit-inner">
          {[45, 135, 225, 315].map((deg, i) => {
            const r = 32
            const x = r * Math.cos((deg * Math.PI) / 180)
            const y = r * Math.sin((deg * Math.PI) / 180)
            return <circle key={i} cx={x} cy={y} r="1.1" fill={COLOR} opacity="0.7" />
          })}
        </g>

        {/* Core glow — bright center */}
        <circle cx="0" cy="0" r="22" fill="url(#aura-core-grad)" className="aura-core" />

        {/* Tiny center pinpoint */}
        <circle cx="0" cy="0" r="2.5" fill={COLOR} className="aura-pinpoint" />
      </svg>

      <style>{`
        .kiosk-aura {
          display: flex; align-items: center; justify-content: center;
          color: ${COLOR};
          filter: drop-shadow(0 0 12px ${COLOR}44);
          transition: filter 600ms ease-in-out, opacity 600ms ease-in-out;
        }
        .kiosk-aura-svg { width: 100%; height: 100%; overflow: visible; }
        .kiosk-aura-svg .aura-halo,
        .kiosk-aura-svg .aura-ripple,
        .kiosk-aura-svg .aura-orbit-outer,
        .kiosk-aura-svg .aura-orbit-inner,
        .kiosk-aura-svg .aura-core,
        .kiosk-aura-svg .aura-pinpoint {
          transform-origin: center;
          transform-box: view-box;
        }

        /* Ripple — circle expands outward and fades */
        @keyframes aura-ripple-pulse {
          0%   { transform: scale(0.35); opacity: 0.8; stroke-width: 1.5; }
          80%  { opacity: 0.05; stroke-width: 0.4; }
          100% { transform: scale(2.4); opacity: 0; stroke-width: 0.3; }
        }
        .aura-ripple { opacity: 0; }

        /* Halo — subtle breathing */
        @keyframes aura-halo-breath {
          0%, 100% { transform: scale(0.95); opacity: 0.9; }
          50%      { transform: scale(1.08); opacity: 1.0; }
        }
        /* Core — pulse */
        @keyframes aura-core-pulse {
          0%, 100% { transform: scale(0.85); }
          50%      { transform: scale(1.1); }
        }
        /* Pinpoint — fast micro-pulse for the center dot */
        @keyframes aura-pinpoint-pulse {
          0%, 100% { opacity: 0.7; }
          50%      { opacity: 1.0; }
        }
        /* Orbits — rotation */
        @keyframes aura-rotate-cw {
          from { transform: rotate(0deg);   }
          to   { transform: rotate(360deg); }
        }
        @keyframes aura-rotate-ccw {
          from { transform: rotate(0deg);    }
          to   { transform: rotate(-360deg); }
        }

        /* ============== STATE: idle ============== */
        .kiosk-aura--idle .aura-halo    { animation: aura-halo-breath 6s ease-in-out infinite; }
        .kiosk-aura--idle .aura-core    { animation: aura-core-pulse 4s ease-in-out infinite; }
        .kiosk-aura--idle .aura-pinpoint{ animation: aura-pinpoint-pulse 4s ease-in-out infinite; }
        .kiosk-aura--idle .aura-orbit-outer { animation: aura-rotate-cw 35s linear infinite; }
        .kiosk-aura--idle .aura-orbit-inner { animation: aura-rotate-ccw 50s linear infinite; }
        .kiosk-aura--idle .aura-ripple-1 { animation: aura-ripple-pulse 5s ease-out infinite; }
        .kiosk-aura--idle .aura-ripple-2 { animation: aura-ripple-pulse 5s ease-out infinite; animation-delay: 1.66s; }
        .kiosk-aura--idle .aura-ripple-3 { animation: aura-ripple-pulse 5s ease-out infinite; animation-delay: 3.33s; }

        /* ============== STATE: listening ============== */
        .kiosk-aura--listening { filter: drop-shadow(0 0 18px ${COLOR}77); }
        .kiosk-aura--listening .aura-halo    { animation: aura-halo-breath 2.5s ease-in-out infinite; }
        .kiosk-aura--listening .aura-core    { animation: aura-core-pulse 1.6s ease-in-out infinite; }
        .kiosk-aura--listening .aura-pinpoint{ animation: aura-pinpoint-pulse 1.6s ease-in-out infinite; }
        .kiosk-aura--listening .aura-orbit-outer { animation: aura-rotate-cw 18s linear infinite; }
        .kiosk-aura--listening .aura-orbit-inner { animation: aura-rotate-ccw 26s linear infinite; }
        .kiosk-aura--listening .aura-ripple-1 { animation: aura-ripple-pulse 2.2s ease-out infinite; }
        .kiosk-aura--listening .aura-ripple-2 { animation: aura-ripple-pulse 2.2s ease-out infinite; animation-delay: 0.73s; }
        .kiosk-aura--listening .aura-ripple-3 { animation: aura-ripple-pulse 2.2s ease-out infinite; animation-delay: 1.46s; }

        /* ============== STATE: speaking ============== */
        .kiosk-aura--speaking { filter: drop-shadow(0 0 24px ${COLOR}aa); }
        .kiosk-aura--speaking .aura-halo    { animation: aura-halo-breath 1.2s ease-in-out infinite; }
        .kiosk-aura--speaking .aura-core    { animation: aura-core-pulse 0.9s ease-in-out infinite; }
        .kiosk-aura--speaking .aura-pinpoint{ animation: aura-pinpoint-pulse 0.9s ease-in-out infinite; }
        .kiosk-aura--speaking .aura-orbit-outer { animation: aura-rotate-cw 12s linear infinite; }
        .kiosk-aura--speaking .aura-orbit-inner { animation: aura-rotate-ccw 18s linear infinite; }
        .kiosk-aura--speaking .aura-ripple-1 { animation: aura-ripple-pulse 1.4s ease-out infinite; }
        .kiosk-aura--speaking .aura-ripple-2 { animation: aura-ripple-pulse 1.4s ease-out infinite; animation-delay: 0.46s; }
        .kiosk-aura--speaking .aura-ripple-3 { animation: aura-ripple-pulse 1.4s ease-out infinite; animation-delay: 0.93s; }

        /* ============== STATE: thinking ============== */
        /* Rotation-only — no ripple or pulse, gives a "processing" feel */
        .kiosk-aura--thinking .aura-orbit-outer { animation: aura-rotate-cw 7s linear infinite; }
        .kiosk-aura--thinking .aura-orbit-inner { animation: aura-rotate-ccw 9s linear infinite; }
        .kiosk-aura--thinking .aura-core    { opacity: 0.85; }
        .kiosk-aura--thinking .aura-pinpoint{ opacity: 0.9; }
        .kiosk-aura--thinking .aura-halo    { opacity: 0.6; }

        /* ============== STATE: offline ============== */
        .kiosk-aura--offline { filter: none; opacity: 0.35; }
      `}</style>
    </div>
  )
}
