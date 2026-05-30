import React, { Suspense, useEffect, useMemo, useRef } from 'react'
import { Canvas, useFrame } from '@react-three/fiber'
import { useGLTF, Center } from '@react-three/drei'
import { createAudioAnalyser } from 'livekit-client'
import * as THREE from 'three'

// JARVIS's face rendered IN the kiosk with WebGL — no Blender at runtime.
// Loads a GLB of the FaceCap head (exported once from Blender, includes the
// jawOpen morph at target index 24) and drives that morph from a jaw value
// supplied each frame by getJaw() (0..1). No per-frame React state.
const MODEL_URL = '/jarvis_head.glb'
useGLTF.preload(MODEL_URL)

// Look/orientation knobs — tuned via headless screenshots.
const HEAD_ROT = [Math.PI / 2, 0, 0]  // radians, applied to the head group
const SKIN_TINT = '#cf9468'           // warm golden-caramel (multiplies texture)

function Head({ getJaw }) {
  const { scene } = useGLTF(MODEL_URL)
  const headRef = useRef(null)
  const jawIdxRef = useRef(null)

  // Find the head mesh (the one carrying the jaw morph), tint the skin.
  useMemo(() => {
    scene.traverse((o) => {
      if (!o.isMesh) return
      o.frustumCulled = false
      if (o.morphTargetDictionary && 'target_24' in o.morphTargetDictionary) {
        headRef.current = o
        jawIdxRef.current = o.morphTargetDictionary['target_24']
        // open the eyes a touch (ARKit eyeWide = target_17/18) — static pose
        const inf = o.morphTargetInfluences
        const eL = o.morphTargetDictionary['target_17']
        const eR = o.morphTargetDictionary['target_18']
        if (inf && eL != null) inf[eL] = 0.55
        if (inf && eR != null) inf[eR] = 0.55
        if (o.material) {
          o.material.color = new THREE.Color(SKIN_TINT)
          o.material.roughness = 0.72
          o.material.metalness = 0.0
        }
      }
    })
  }, [scene])

  useFrame(() => {
    const h = headRef.current
    const idx = jawIdxRef.current
    if (h && idx != null && h.morphTargetInfluences) {
      const target = Math.max(0, Math.min(1, getJaw ? getJaw() : 0))
      const cur = h.morphTargetInfluences[idx] || 0
      // open faster than close, like speech
      const k = target > cur ? 0.4 : 0.25
      h.morphTargetInfluences[idx] = cur + (target - cur) * k
    }
  })

  return (
    <Center>
      <group rotation={HEAD_ROT}>
        <primitive object={scene} />
      </group>
    </Center>
  )
}

// Drive the jaw from a LiveKit audio track's level. Returns a ref holding the
// current 0..1 jaw value, updated off-React (rAF) so there are NO per-frame
// React re-renders. Pass the result to FaceWebGL's getJaw. JARVIS's agent track
// carries its TTS, so this is the voice level.
//
// IMPORTANT: a hand-rolled createMediaStreamSource on a *remote* WebRTC track
// returns silence in Chromium (known issue) — LiveKit's createAudioAnalyser
// handles pulling audio from a remote track correctly, so we use that.
const JAW_GAIN = 6.0
export function useJawFromTrack(audioTrack) {
  const jawRef = useRef(0)
  useEffect(() => {
    // useTracks yields a TrackReference; createAudioAnalyser wants the track.
    const track = audioTrack?.publication?.track ?? audioTrack
    if (!track || track.kind !== 'audio' || !track.mediaStreamTrack) return
    let raf
    let handle
    try {
      handle = createAudioAnalyser(track, {
        fftSize: 256,
        smoothingTimeConstant: 0.25,
      })
    } catch (e) {
      return
    }
    const tick = () => {
      const vol = handle.calculateVolume()   // 0..1 RMS, remote-track safe
      jawRef.current = Math.max(0, Math.min(1, vol * JAW_GAIN))
      raf = requestAnimationFrame(tick)
    }
    tick()
    return () => {
      cancelAnimationFrame(raf)
      try { handle.cleanup() } catch {}
    }
  }, [audioTrack])
  return jawRef
}

export function FaceWebGL({ size, getJaw }) {
  return (
    <Canvas
      style={{ width: size, height: size, background: 'transparent' }}
      camera={{ position: [0, 0.02, 0.92], fov: 30 }}
      gl={{ alpha: true, antialias: true }}
      dpr={[1, 2]}
    >
      <ambientLight intensity={0.55} />
      <directionalLight position={[1.2, 1.4, 2.0]} intensity={2.2} color="#fff3e3" />
      <directionalLight position={[-1.4, 0.6, 1.2]} intensity={0.7} color="#a8d4ff" />
      <Suspense fallback={null}>
        <Head getJaw={getJaw} />
      </Suspense>
    </Canvas>
  )
}
