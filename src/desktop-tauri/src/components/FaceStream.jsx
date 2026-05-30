import React, { useEffect, useRef } from 'react'

// JARVIS's live Blender face as a native MJPEG <img>. The webview refreshes the
// bitmap itself — NO per-frame React state (the voice reactor sphere was removed
// for exactly that cost; see .claude/rules/desktop-tauri.md).
//
// Health: onError -> not healthy; first successful onLoad -> healthy; if no frame
// arrives within CONNECT_TIMEOUT_MS of mount, report unhealthy so the parent
// shows the ring. The parent keeps this mounted (hidden) during fallback so the
// stream can recover on its own.
const FACE_STREAM_URL = 'http://127.0.0.1:8770/stream.mjpg'
const CONNECT_TIMEOUT_MS = 2500

export function FaceStream({ size, onHealth }) {
  const imgRef = useRef(null)

  useEffect(() => {
    const img = imgRef.current
    if (img) img.src = `${FACE_STREAM_URL}?t=${Date.now()}`
    const timer = setTimeout(() => {
      const ok = img && img.complete && img.naturalWidth > 0
      onHealth(Boolean(ok))
    }, CONNECT_TIMEOUT_MS)
    return () => clearTimeout(timer)
  }, [onHealth])

  return (
    <img
      ref={imgRef}
      width={size}
      height={size}
      alt=""
      onError={() => onHealth(false)}
      onLoad={() => onHealth(true)}
      style={{
        width: size,
        height: size,
        objectFit: 'cover',
        display: 'block',
        borderRadius: '50%',
      }}
    />
  )
}
