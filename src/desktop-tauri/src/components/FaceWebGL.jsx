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

function Head({ getWeights }) {
  const { scene } = useGLTF(MODEL_URL)
  const headRef = useRef(null)
  const idxByTargetRef = useRef({})   // 'target_24' -> influence index

  // Find the head mesh (carries the jaw morph), build the target->index
  // map once, tint the skin, set a static eyeWide pose.
  useMemo(() => {
    scene.traverse((o) => {
      if (!o.isMesh) return
      o.frustumCulled = false
      if (o.morphTargetDictionary && 'target_24' in o.morphTargetDictionary) {
        headRef.current = o
        idxByTargetRef.current = o.morphTargetDictionary
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
    if (!h || !h.morphTargetInfluences) return
    const dict = idxByTargetRef.current
    const targets = (getWeights && getWeights()) || {}
    // Mouth/viseme morphs we drive every frame (eyes/brows excluded so the
    // static eyeWide + idle blinks aren't fought).
    const MOUTH = [24, 28, 29, 36, 37, 38, 43, 44, 45, 46, 47, 48, 49, 50, 51]
    for (const n of MOUTH) {
      const key = 'target_' + n
      const i = dict[key]
      if (i == null) continue
      const target = Math.max(0, Math.min(1, targets[key] || 0))
      const cur = h.morphTargetInfluences[i] || 0
      const k = target > cur ? 0.4 : 0.25     // open fast, close slower
      h.morphTargetInfluences[i] = cur + (target - cur) * k
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
