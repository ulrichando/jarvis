import React, { Suspense, useEffect, useMemo, useRef } from 'react'
import { Canvas, useFrame } from '@react-three/fiber'
import { useGLTF, Center } from '@react-three/drei'
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

  // DEV diagnostic: report the model bbox via document.title for headless checks.
  useEffect(() => {
    const box = new THREE.Box3().setFromObject(scene)
    const s = new THREE.Vector3(), c = new THREE.Vector3()
    box.getSize(s); box.getCenter(c)
    document.title = `GLB size=${s.x.toFixed(3)},${s.y.toFixed(3)},${s.z.toFixed(3)} ctr=${c.x.toFixed(3)},${c.y.toFixed(3)},${c.z.toFixed(3)} jaw=${jawIdxRef.current}`
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

// Drive the jaw from a LiveKit audio track's level via a Web Audio analyser.
// Returns a ref holding the current 0..1 jaw value, updated off-React (rAF) so
// there are no per-frame React re-renders. Pass the result to FaceWebGL's
// getJaw. JARVIS's agent track carries its TTS, so this is the voice level.
const JAW_GAIN = 5.5
export function useJawFromTrack(audioTrack) {
  const jawRef = useRef(0)
  useEffect(() => {
    const mst = audioTrack?.mediaStreamTrack
    if (!mst) return
    let ctx, raf, src, analyser
    try {
      ctx = new (window.AudioContext || window.webkitAudioContext)()
      src = ctx.createMediaStreamSource(new MediaStream([mst]))
      analyser = ctx.createAnalyser()
      analyser.fftSize = 512
      analyser.smoothingTimeConstant = 0.3
      src.connect(analyser)
      ctx.resume?.()
    } catch (e) {
      return
    }
    const data = new Uint8Array(analyser.fftSize)
    const tick = () => {
      analyser.getByteTimeDomainData(data)
      let sum = 0
      for (let i = 0; i < data.length; i++) {
        const v = (data[i] - 128) / 128
        sum += v * v
      }
      const rms = Math.sqrt(sum / data.length)
      jawRef.current = Math.max(0, Math.min(1, rms * JAW_GAIN))
      raf = requestAnimationFrame(tick)
    }
    tick()
    return () => {
      cancelAnimationFrame(raf)
      try { src.disconnect() } catch {}
      try { ctx.close() } catch {}
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
