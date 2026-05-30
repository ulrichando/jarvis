import React, { useEffect, useRef, useState } from 'react'

// JARVIS's live Blender face as a native MJPEG <img>. The webview refreshes the
// bitmap itself — NO per-frame React state (the voice reactor sphere was removed
// for exactly that cost; see .claude/rules/desktop-tauri.md).
//
// This is now the kiosk's ONLY visualizer (the ring was removed), so it
// auto-reconnects: if the stream isn't up yet or drops, it re-points the <img>
// every RETRY_MS until frames flow, instead of going blank forever.
const FACE_STREAM_URL = 'http://127.0.0.1:8770/stream.mjpg'
const RETRY_MS = 1500

export function FaceStream({ size, onHealth }) {
  const imgRef = useRef(null)
  const [down, setDown] = useState(false)   // occasional (failure) state, not per-frame

  // (Re)connect on mount and whenever a retry is requested.
  useEffect(() => {
    const img = imgRef.current
    if (img) img.src = `${FACE_STREAM_URL}?t=${Date.now()}`
  }, [])

  // Retry loop: while down, re-point the stream every RETRY_MS.
  useEffect(() => {
    if (!down) return
    const id = setInterval(() => {
      const img = imgRef.current
      if (img) img.src = `${FACE_STREAM_URL}?t=${Date.now()}`
    }, RETRY_MS)
    return () => clearInterval(id)
  }, [down])

  const report = (ok) => {
    setDown(!ok)
    onHealth?.(ok)
  }

  return (
    <img
      ref={imgRef}
      width={size}
      height={size}
      alt=""
      onError={() => report(false)}
      onLoad={() => report(true)}
      style={{
        width: size,
        height: size,
        objectFit: 'cover',
        display: down ? 'none' : 'block',   // black backdrop shows through while down
        borderRadius: '50%',
      }}
    />
  )
}
