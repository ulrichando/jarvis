import React, { Suspense, useEffect, useMemo, useRef } from 'react'
import { Canvas, useFrame } from '@react-three/fiber'
import { useGLTF, Center } from '@react-three/drei'
import * as THREE from 'three'

// JARVIS's face rendered IN the kiosk with WebGL — no Blender at runtime.
// Loads a GLB of the FaceCap head (exported once from Blender, includes ARKit
// viseme morphs) and drives mouth morphs from per-frame weights supplied by
// getWeights() ({target_N: 0..1}). No per-frame React state.
const MODEL_URL = '/jarvis_head.glb'
useGLTF.preload(MODEL_URL)

// Look/orientation knobs — tuned via headless screenshots.
const HEAD_ROT = [Math.PI / 2, 0, 0]  // radians, applied to the head group
const SKIN_TINT = '#cf9468'           // warm golden-caramel (multiplies texture)

// Mouth/viseme morph indices driven every frame (eyes/brows excluded so the
// static eyeWide + idle blinks aren't fought). Module-level so it isn't
// reallocated per frame (no per-frame allocation in voice UI).
const MOUTH = [24, 28, 29, 36, 37, 38, 43, 44, 45, 46, 47, 48, 49, 50, 51]

// Expression morphs (brows / cheeks / frown) driven each frame from /face.
// eyeWide (17/18) is handled separately with a 0.55 baseline so the wide-eyed
// look persists and expressions modulate around it. Module-level (no realloc).
const EXPRESSION = [0, 1, 2, 3, 4, 20, 21, 39, 40]

function Head({ getWeights }) {
  const { scene } = useGLTF(MODEL_URL)
  const headRef = useRef(null)
  const idxByTargetRef = useRef({})   // 'target_24' -> influence index
  const groupRef = useRef(null)        // head group, for sway
  const blinkRef = useRef({ next: 2.0, t: -1 })  // next blink time, active start
  const clockRef = useRef(0)
  const browRef = useRef({ next: 5.0, t: -1 })   // idle brow flick
  const dartRef = useRef({ next: 4.0, t: -1, dir: 1 })  // idle eye dart

  // Find the head mesh (carries the jaw morph), build the target->index
  // map once, tint the skin, set a static eyeWide pose.
  useMemo(() => {
    scene.traverse((o) => {
      if (!o.isMesh) return
      o.frustumCulled = false
      if (o.morphTargetDictionary && 'target_24' in o.morphTargetDictionary) {
        headRef.current = o
        idxByTargetRef.current = o.morphTargetDictionary
        if (o.material) {
          o.material.color = new THREE.Color(SKIN_TINT)
          o.material.roughness = 0.72
          o.material.metalness = 0.0
        }
      }
    })
  }, [scene])

  useFrame((_, delta) => {
    const h = headRef.current
    if (!h || !h.morphTargetInfluences) return
    const dict = idxByTargetRef.current
    const inf = h.morphTargetInfluences
    const targets = (getWeights && getWeights()) || {}
    for (const n of MOUTH) {
      const key = 'target_' + n
      const i = dict[key]
      if (i == null) continue
      const target = Math.max(0, Math.min(1, targets[key] || 0))
      const cur = inf[i] || 0
      const k = target > cur ? 0.4 : 0.25
      inf[i] = cur + (target - cur) * k
    }

    // Expression morphs (brows/cheeks/frown) — ease gently toward /face value.
    for (const n of EXPRESSION) {
      const key = 'target_' + n
      const i = dict[key]
      if (i == null) continue
      const target = Math.max(0, Math.min(1, targets[key] || 0))
      const cur = inf[i] || 0
      inf[i] = cur + (target - cur) * 0.2
    }
    // eyeWide: 0.55 baseline + expression modulation (blink overrides below).
    const eyeTarget = Math.max(0, Math.min(1, 0.55 + (targets['target_17'] || 0)))
    const ewL = dict['target_17'], ewR = dict['target_18']
    if (ewL != null) inf[ewL] = inf[ewL] + (eyeTarget - inf[ewL]) * 0.2
    if (ewR != null) inf[ewR] = inf[ewR] + (eyeTarget - inf[ewR]) * 0.2

    // ── idle life ──────────────────────────────────────────────
    const now = (clockRef.current += delta)
    // Blink: schedule every 3–6 s; a blink is a ~120 ms close→open.
    const bl = dict['target_13'], br = dict['target_14']
    const blink = blinkRef.current
    if (blink.t < 0 && now >= blink.next) { blink.t = now }
    let blinkVal = 0
    if (blink.t >= 0) {
      const p = (now - blink.t) / 0.12           // 0..1 over 120 ms
      if (p >= 1) { blink.t = -1; blink.next = now + 3 + Math.random() * 3 }
      else { blinkVal = Math.sin(p * Math.PI) }  // 0→1→0
    }
    if (bl != null) inf[bl] = blinkVal
    if (br != null) inf[br] = blinkVal

    // Head sway: gentle, damped while the mouth is active so it doesn't
    // fight visemes.
    const g = groupRef.current
    if (g) {
      const jaw = inf[dict['target_24']] || 0
      const amp = (1 - Math.min(1, jaw * 2)) * 0.026   // ~±1.5° at rest
      g.rotation.z = HEAD_ROT[2] + Math.sin(now * 0.6) * amp
      g.rotation.y = Math.sin(now * 0.43) * amp * 0.6
    }

    // Idle brow flick — only when no content expression is driving the brows.
    const hasExprBrow = ((targets['target_0'] || 0) + (targets['target_3'] || 0) + (targets['target_1'] || 0)) > 0.01
    const ib = dict['target_0']
    const brow = browRef.current
    if (!hasExprBrow && ib != null) {
      if (brow.t < 0 && now >= brow.next) { brow.t = now }
      if (brow.t >= 0) {
        const p = (now - brow.t) / 0.4
        if (p >= 1) { brow.t = -1; brow.next = now + 4 + Math.random() * 5 }
        else inf[ib] = Math.max(inf[ib], Math.sin(p * Math.PI) * 0.18)
      }
    }
    // Idle eye dart — brief subtle horizontal glance every ~4–8 s.
    const dart = dartRef.current
    if (dart.t < 0 && now >= dart.next) { dart.t = now; dart.dir = Math.random() < 0.5 ? -1 : 1 }
    let gaze = 0
    if (dart.t >= 0) {
      const p = (now - dart.t) / 0.5
      if (p >= 1) { dart.t = -1; dart.next = now + 4 + Math.random() * 4 }
      else gaze = Math.sin(p * Math.PI) * 0.25 * dart.dir
    }
    const lo = dict['target_11'], ro = dict['target_12']  // eyeLookOutLeft/Right
    const li = dict['target_9'],  ri = dict['target_10']  // eyeLookInLeft/Right
    if (lo != null) inf[lo] = Math.max(0, -gaze)
    if (ro != null) inf[ro] = Math.max(0,  gaze)
    if (li != null) inf[li] = Math.max(0,  gaze)
    if (ri != null) inf[ri] = Math.max(0, -gaze)
  })

  return (
    <Center>
      <group ref={groupRef} rotation={HEAD_ROT}>
        <primitive object={scene} />
      </group>
    </Center>
  )
}

export function FaceWebGL({ size, getWeights }) {
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
        <Head getWeights={getWeights} />
      </Suspense>
    </Canvas>
  )
}
