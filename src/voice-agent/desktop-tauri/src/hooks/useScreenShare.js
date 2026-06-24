// Webview-side screen-share hook.
//
// Owns a lazy LiveKit room connection used ONLY to publish a
// screen-share track via the JS SDK's setScreenShareEnabled(true) —
// which on Linux triggers xdg-desktop-portal (the OS-native picker
// behind Google Meet / Zoom Web / Teams). The voice-client's
// existing GET /screen-share/token endpoint mints a JWT with a
// distinct identity ("desktop-ulrich-screen") so this connection
// doesn't collide with the voice-client's main room participant.
//
// Why a separate hook instead of folding into useVoiceClient:
// useVoiceClient is HTTP-polling only — it never opens a LiveKit
// room. The screen-share path requires a real LiveKit Room
// participant for getDisplayMedia to publish through. Keeping the
// concerns separate means the voice-client polling stays unchanged
// and the screen-share connection only exists while the user is
// actively sharing (lazy connect on first start, disconnect on
// stop).
import { useCallback, useEffect, useRef, useState } from 'react'
import {
  Room,
  RoomEvent,
  Track,
  ConnectionState,
} from 'livekit-client'

const TOKEN_URL = 'http://127.0.0.1:8767/screen-share/token'

/** @returns {{
 *   active: boolean,
 *   connecting: boolean,
 *   error: string|null,
 *   start: () => Promise<void>,
 *   stop:  () => Promise<void>,
 *   toggle: () => Promise<void>,
 * }} */
export function useScreenShare() {
  const [active, setActive]         = useState(false)
  const [connecting, setConnecting] = useState(false)
  const [error, setError]           = useState(null)
  const roomRef = useRef(/** @type {Room|null} */ (null))

  // Tear down the room cleanly on unmount so we don't leak a
  // participant if the user closes the window mid-share.
  useEffect(() => {
    return () => {
      const r = roomRef.current
      roomRef.current = null
      if (r) {
        try { r.disconnect() } catch { /* ignore */ }
      }
    }
  }, [])

  const _ensureRoom = useCallback(async () => {
    if (roomRef.current && roomRef.current.state === ConnectionState.Connected) {
      return roomRef.current
    }
    // Fresh mint each connect — token TTL is 24h server-side, but
    // making this lazy means a stale token from a stopped session
    // never reaches the connect path.
    const resp = await fetch(TOKEN_URL, { cache: 'no-store' })
    if (!resp.ok) {
      throw new Error(`token endpoint ${resp.status}`)
    }
    const { url, token } = await resp.json()
    if (!url || !token) {
      throw new Error('token endpoint returned empty url/token')
    }
    // Defaults are fine — we're publish-only, no need to crank
    // adaptive stream / dynacast.
    const room = new Room({
      // We never subscribe in this hook, but a default subscribe
      // shouldn't hurt — leaving the SDK to its sensible defaults.
    })
    // Track the publish lifecycle so the UI flips back to "not
    // sharing" when the OS-picker's "Stop sharing" button is hit
    // (the SDK detects the underlying MediaStreamTrack ending and
    // emits LocalTrackUnpublished).
    room.on(RoomEvent.LocalTrackUnpublished, (pub) => {
      if (pub.source === Track.Source.ScreenShare) {
        setActive(false)
      }
    })
    room.on(RoomEvent.Disconnected, () => {
      setActive(false)
    })
    await room.connect(url, token)
    roomRef.current = room
    return room
  }, [])

  const start = useCallback(async () => {
    setError(null)
    setConnecting(true)
    try {
      const room = await _ensureRoom()
      // setScreenShareEnabled(true) calls getDisplayMedia() under the
      // hood — that's what triggers the OS picker. The user picks a
      // monitor/window/full screen, the SDK publishes the resulting
      // track as Source.ScreenShare. screen_share_observer on the
      // agent side subscribes to any SOURCE_SCREENSHARE track in the
      // room and starts describing.
      await room.localParticipant.setScreenShareEnabled(true)
      setActive(true)
    } catch (e) {
      // User-cancelled the picker → NotAllowedError. That's a
      // normal flow, not an error to display.
      const msg = String(e?.name || e?.message || e)
      if (msg.includes('NotAllowedError') || msg.includes('AbortError')) {
        setError(null)
      } else {
        setError(msg)
      }
      setActive(false)
    } finally {
      setConnecting(false)
    }
  }, [_ensureRoom])

  const stop = useCallback(async () => {
    setError(null)
    const room = roomRef.current
    if (!room) {
      setActive(false)
      return
    }
    try {
      await room.localParticipant.setScreenShareEnabled(false)
    } catch (e) {
      // Best-effort; even on error, drop the connection so the
      // user can re-share without a stale state hanging around.
      setError(String(e?.message || e))
    }
    try {
      await room.disconnect()
    } catch { /* ignore */ }
    roomRef.current = null
    setActive(false)
  }, [])

  const toggle = useCallback(async () => {
    if (active) await stop()
    else        await start()
  }, [active, start, stop])

  return { active, connecting, error, start, stop, toggle }
}
