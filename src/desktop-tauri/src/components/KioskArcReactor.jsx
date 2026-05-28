import React from 'react'

// Pure SVG + CSS-keyframe arc reactor. No per-frame React (the
// reactor sphere rule from CLAUDE.md applies — animation is entirely
// CSS-driven, state prop only swaps the active className).
//
// State values:
//   "offline"   — dim, no animation (overlay's voice client not connected)
//   "idle"      — gentle pulse, slow rotate
//   "listening" — faster pulse, brighter glow
//   "speaking"  — fastest pulse
//   "thinking"  — rotation only, no pulse
//
// Colour: #1FD5F9 matches LiveKit AgentAudioVisualizerAura default.
const COLOR = '#1FD5F9'

export default function KioskArcReactor({ state = 'idle', size = 320 }) {
  const cls = `kiosk-arc kiosk-arc--${state}`
  return (
    <div className={cls} style={{ width: size, height: size }}>
      <svg viewBox="-50 -50 100 100" className="kiosk-arc-svg" xmlns="http://www.w3.org/2000/svg">
        {/* Center dot */}
        <circle cx="0" cy="0" r="3" fill={COLOR} className="kiosk-arc-center" />

        {/* Inner ring */}
        <circle cx="0" cy="0" r="8" fill="none" stroke={COLOR} strokeWidth="0.8"
                className="kiosk-arc-inner" />

        {/* Dotted ring */}
        <circle cx="0" cy="0" r="14" fill="none" stroke={COLOR} strokeWidth="0.7"
                strokeDasharray="1.3 2.5" className="kiosk-arc-dotted" />

        {/* Outer broken arc segments — three groups, one full circle worth */}
        <g className="kiosk-arc-outer">
          <circle cx="0" cy="0" r="22" fill="none" stroke={COLOR} strokeWidth="1.4"
                  strokeDasharray="30 16" strokeDashoffset="0" />
        </g>

        {/* Faint outermost ring for depth */}
        <circle cx="0" cy="0" r="32" fill="none" stroke={COLOR} strokeWidth="0.3"
                opacity="0.4" className="kiosk-arc-edge" />
      </svg>
      <style>{`
        .kiosk-arc {
          display: flex; align-items: center; justify-content: center;
          color: ${COLOR};
          filter: drop-shadow(0 0 6px ${COLOR}55);
        }
        .kiosk-arc-svg { width: 100%; height: 100%; overflow: visible; }
        .kiosk-arc-svg * { transform-origin: 0 0; transform-box: fill-box; }

        .kiosk-arc-inner { transform-origin: center; transform-box: view-box; }
        .kiosk-arc-dotted { transform-origin: center; transform-box: view-box; }
        .kiosk-arc-outer { transform-origin: center; transform-box: view-box; }
        .kiosk-arc-edge { transform-origin: center; transform-box: view-box; }

        @keyframes kiosk-arc-pulse {
          0%, 100% { opacity: 0.55; }
          50%      { opacity: 1.0; }
        }
        @keyframes kiosk-arc-rotate {
          from { transform: rotate(0deg); }
          to   { transform: rotate(360deg); }
        }
        @keyframes kiosk-arc-rotate-ccw {
          from { transform: rotate(0deg); }
          to   { transform: rotate(-360deg); }
        }

        /* Idle: slow pulse on inner rings, slow rotate on outer */
        .kiosk-arc--idle .kiosk-arc-center,
        .kiosk-arc--idle .kiosk-arc-inner,
        .kiosk-arc--idle .kiosk-arc-dotted {
          animation: kiosk-arc-pulse 4s ease-in-out infinite;
        }
        .kiosk-arc--idle .kiosk-arc-outer {
          animation: kiosk-arc-rotate 30s linear infinite;
        }
        .kiosk-arc--idle .kiosk-arc-dotted {
          animation: kiosk-arc-pulse 4s ease-in-out infinite,
                     kiosk-arc-rotate-ccw 45s linear infinite;
        }

        /* Listening: faster pulse, brighter */
        .kiosk-arc--listening {
          filter: drop-shadow(0 0 10px ${COLOR}88);
        }
        .kiosk-arc--listening .kiosk-arc-center,
        .kiosk-arc--listening .kiosk-arc-inner,
        .kiosk-arc--listening .kiosk-arc-dotted {
          animation: kiosk-arc-pulse 1.5s ease-in-out infinite;
        }
        .kiosk-arc--listening .kiosk-arc-outer {
          animation: kiosk-arc-rotate 15s linear infinite;
        }
        .kiosk-arc--listening .kiosk-arc-dotted {
          animation: kiosk-arc-pulse 1.5s ease-in-out infinite,
                     kiosk-arc-rotate-ccw 25s linear infinite;
        }

        /* Speaking: fastest pulse */
        .kiosk-arc--speaking {
          filter: drop-shadow(0 0 14px ${COLOR}aa);
        }
        .kiosk-arc--speaking .kiosk-arc-center,
        .kiosk-arc--speaking .kiosk-arc-inner,
        .kiosk-arc--speaking .kiosk-arc-dotted {
          animation: kiosk-arc-pulse 0.8s ease-in-out infinite;
        }
        .kiosk-arc--speaking .kiosk-arc-outer {
          animation: kiosk-arc-rotate 10s linear infinite;
        }
        .kiosk-arc--speaking .kiosk-arc-dotted {
          animation: kiosk-arc-pulse 0.8s ease-in-out infinite,
                     kiosk-arc-rotate-ccw 18s linear infinite;
        }

        /* Thinking: rotation only, no pulse */
        .kiosk-arc--thinking .kiosk-arc-outer {
          animation: kiosk-arc-rotate 6s linear infinite;
        }
        .kiosk-arc--thinking .kiosk-arc-dotted {
          animation: kiosk-arc-rotate-ccw 9s linear infinite;
        }

        /* Offline: dim, no animation */
        .kiosk-arc--offline {
          filter: none;
          opacity: 0.35;
        }
      `}</style>
    </div>
  )
}
