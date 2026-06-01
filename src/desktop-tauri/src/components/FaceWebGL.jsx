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
const HEAD_BASE_ROT = [Math.PI / 2, 0, 0]  // base orientation (faces camera)
const HEAD_YAW_MAX   = 0.18   // ±10° left/right when tracking
const HEAD_PITCH_MAX = 0.12   // ±7° up/down when tracking
const HEAD_EASE      = 0.08   // easing factor per frame (~60fps)
const SKIN_TINT = '#cf9468'   // warm golden-caramel (multiplies texture)

// Mouth/viseme morph indices driven every frame (eyes/brows excluded so the
// static eyeWide + idle blinks aren't fought). Module-level so it isn't
// reallocated per frame (no per-frame allocation in voice UI).
const MOUTH = [24, 28, 29, 36, 37, 38, 43, 44, 45, 46, 47, 48, 49, 50, 51]

// Expression morphs (brows / cheeks / frown) driven each frame from /face.
// eyeWide (17/18) is handled separately with a 0.55 baseline so the wide-eyed
// look persists and expressions modulate around it. Module-level (no realloc).
const EXPRESSION = [0, 1, 2, 3, 4, 20, 21, 39, 40]
// Resting-face defaults — a friendly look at rest, not a wide-eyed stare.
const SMILE_REST  = 0.28   // baseline mouthSmile (37/38): gentle default smile
const EYELID_REST = 0.12   // baseline eyeBlink (13/14): upper lid relaxed toward the iris
const HEAD_ROLL   = -0.095 // constant roll (rad) — levels the GLB's baked ~5.5° head tilt (measured; ?roll= adds, dev)

function Head({ getWeights, personTracker }) {
  const { scene } = useGLTF(MODEL_URL)
  const headRef = useRef(null)
  const idxByTargetRef = useRef({})   // 'target_24' -> influence index
  const groupRef = useRef(null)        // head group, for rotation
  const blinkRef = useRef({ next: 2.0, t: -1 })  // next blink time, active start
  const clockRef = useRef(0)
  const browRef = useRef({ next: 5.0, t: -1 })   // idle brow flick
  const dartRef = useRef({ next: 4.0, t: -1, dir: 1 })  // idle eye dart
  const headYawRef = useRef(0)          // current smoothed head yaw
  const headPitchRef = useRef(0)        // current smoothed head pitch
  const rollFix = HEAD_ROLL + (parseFloat(new URLSearchParams(window.location.search).get('roll')) || 0)

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
      let target = Math.max(0, Math.min(1, targets[key] || 0))
      if (n === 37 || n === 38) target = Math.max(target, SMILE_REST)   // default gentle smile
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
    // eyeWide: NO baseline (relaxed eyes, not a stare) — expression widens only.
    const eyeTarget = Math.max(0, Math.min(1, targets['target_17'] || 0))
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
    if (bl != null) inf[bl] = Math.max(EYELID_REST, blinkVal)
    if (br != null) inf[br] = Math.max(EYELID_REST, blinkVal)

    // Head stays level (no sway). The screen-space roll correction is applied
    // on the OUTER wrapper group in the render below (unambiguous: camera looks
    // down Z, so an outer Z-rotation rolls the head in the image plane).

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
    // ── Gaze: person tracker overrides idle eye darts ─────────────
    // When the voice-client's person tracker detects a face, the eyes
    // follow it (horizontal + vertical). Otherwise fall back to idle
    // random glances. Tracker values are 0..1 normalized; map to eye
    // morph range (≈ ±0.30 horizontal, ≈ ±0.20 vertical).
    let gazeX = 0, gazeY = 0
    const pt = personTracker
    if (pt && pt.person_detected && pt.primary_face) {
      const cx = pt.primary_face.center_x  // 0..1, 0.5 = center
      const cy = pt.primary_face.center_y
      gazeX = (cx - 0.5) * 0.60   // ±0.30 max
      gazeY = (0.5 - cy) * 0.40   // ±0.20 max (invert: top of frame = look up)
    } else {
      // Idle eye dart — brief subtle horizontal glance every ~4–8 s.
      const dart = dartRef.current
      if (dart.t < 0 && now >= dart.next) { dart.t = now; dart.dir = Math.random() < 0.5 ? -1 : 1 }
      if (dart.t >= 0) {
        const p = (now - dart.t) / 0.5
        if (p >= 1) { dart.t = -1; dart.next = now + 4 + Math.random() * 4 }
        else gazeX = Math.sin(p * Math.PI) * 0.25 * dart.dir
      }
    }
    const lo = dict['target_11'], ro = dict['target_12']  // eyeLookOutLeft/Right
    const li = dict['target_9'],  ri = dict['target_10']  // eyeLookInLeft/Right
    const lu = dict['target_15'], ru = dict['target_16']  // eyeLookUpLeft/Right
    const ld = dict['target_17'], rd = dict['target_18']  // eyeLookDown (eyeWide shares 17/18)
    if (lo != null) inf[lo] = Math.max(0, -gazeX)
    if (ro != null) inf[ro] = Math.max(0,  gazeX)
    if (li != null) inf[li] = Math.max(0,  gazeX)
    if (ri != null) inf[ri] = Math.max(0, -gazeX)
    // Vertical gaze (only when tracking; idle darts are horizontal-only)
    if (pt && pt.person_detected && pt.primary_face) {
      if (lu != null) inf[lu] = Math.max(0, -gazeY)
      if (ru != null) inf[ru] = Math.max(0, -gazeY)
      if (ld != null && gazeY > 0) inf[ld] = Math.max(inf[ld] || 0, gazeY)
      if (rd != null && gazeY > 0) inf[rd] = Math.max(inf[rd] || 0, gazeY)
    }

    // ── Head rotation: follow the tracked person ─────────────────
    // Yaw (left/right) from center_x, pitch (up/down) from center_y.
    // Eased smoothly so the head doesn't snap; natural ~500ms settling.
    if (pt && pt.person_detected && pt.primary_face) {
      const targetYaw   = -(pt.primary_face.center_x - 0.5) * HEAD_YAW_MAX * 2
      const targetPitch = (pt.primary_face.center_y - 0.5) * HEAD_PITCH_MAX * 2
      headYawRef.current   += (targetYaw   - headYawRef.current)   * HEAD_EASE
      headPitchRef.current += (targetPitch - headPitchRef.current) * HEAD_EASE
    } else {
      // No person — slowly return head to center
      headYawRef.current   += (0 - headYawRef.current)   * 0.04
      headPitchRef.current += (0 - headPitchRef.current) * 0.04
    }
    if (groupRef.current) {
      groupRef.current.rotation.set(
        HEAD_BASE_ROT[0] + headPitchRef.current,
        HEAD_BASE_ROT[1] + headYawRef.current,
        HEAD_BASE_ROT[2],
      )
    }
  })

  return (
    <Center>
      <group rotation={[0, 0, rollFix]}>
        <group ref={groupRef} rotation={HEAD_BASE_ROT}>
          <primitive object={scene} />
        </group>
      </group>
    </Center>
  )
}

export function FaceWebGL({ size, getWeights, personTracker }) {
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
        <Head getWeights={getWeights} personTracker={personTracker} />
      </Suspense>
    </Canvas>
  )
}
