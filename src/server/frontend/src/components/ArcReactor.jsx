import { useRef, useEffect } from 'react'
import * as THREE from 'three'

/**
 * JARVIS Holographic Sphere — Age of Ultron
 *
 * Dense network of glowing lines mapped onto a sphere,
 * like a city power grid. Lines at multiple depths.
 * Bright orbital bands. Central ring structure.
 * Audio-reactive glow and expansion.
 */

export default function ArcReactor({ state = 'idle', isDesktop = false, audioLevel = 0, theme }) {
  const mountRef = useRef(null)
  const audioRef = useRef(0)
  const stateRef = useRef(state)
  const themeRef = useRef(theme)

  useEffect(() => { audioRef.current = audioLevel }, [audioLevel])
  useEffect(() => { stateRef.current = state }, [state])
  useEffect(() => { themeRef.current = theme }, [theme])

  useEffect(() => {
    if (!mountRef.current) return
    const el = mountRef.current

    // ── Theme color helpers ──
    const hexToInt = (hex) => parseInt(hex.replace('#', ''), 16)
    const t = themeRef.current || { primary: '#00b8d4', glow: '#00e5ff' }
    const primaryInt = hexToInt(t.primary)
    const glowInt = hexToInt(t.glow)
    // Derive a dimmer variant for structure lines
    const pr = (primaryInt >> 16) & 0xff, pg = (primaryInt >> 8) & 0xff, pb = primaryInt & 0xff
    const structInt = ((Math.min(255, pr + 60) << 16) | (Math.min(255, pg + 60) << 8) | Math.min(255, pb + 60))
    // All theme-colored materials tracked for live updates
    const themedMaterials = []

    const scene = new THREE.Scene()
    const camera = new THREE.PerspectiveCamera(50, window.innerWidth / window.innerHeight, 0.1, 100)
    camera.position.z = 10
    camera.lookAt(0, 0, 0)

    const renderer = new THREE.WebGLRenderer({ alpha: true, antialias: true })
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2))
    renderer.setSize(window.innerWidth, window.innerHeight)
    renderer.setClearColor(0x000000, 0)
    el.appendChild(renderer.domElement)

    const globe = new THREE.Group()
    scene.add(globe)
    const R = 1.3

    const lineMat = (opacity) => {
      const m = new THREE.LineBasicMaterial({
        color: structInt,
        transparent: true,
        opacity,
        blending: THREE.NormalBlending,
        depthWrite: false,
        linewidth: 1.5,
      })
      themedMaterials.push({ mat: m, role: 'struct' })
      return m
    }

    // ── 1–3. Grid lines removed — puzzle pieces define the structure now ──

    // ── 3b. 3D spherical puzzle pieces — arc segments on sphere surface ──
    const cellMeshes = []
    // Rings defined by phi (latitude) bands on the sphere
    const puzzleRings = [
      { phi: 0.30, segs: 4,  dPhi: 0.18 },
      { phi: 0.55, segs: 6,  dPhi: 0.18 },
      { phi: 0.80, segs: 8,  dPhi: 0.18 },
      { phi: 1.05, segs: 10, dPhi: 0.18 },
      { phi: 1.30, segs: 12, dPhi: 0.18 },
      { phi: 1.55, segs: 14, dPhi: 0.18 },
      { phi: 1.80, segs: 10, dPhi: 0.18 },
      { phi: 2.05, segs: 8,  dPhi: 0.18 },
      { phi: 2.30, segs: 6,  dPhi: 0.18 },
      { phi: 2.55, segs: 4,  dPhi: 0.18 },
    ]
    const gapAngle = 0.08
    const arcRes = 6
    const phiRes = 3
    const puzzleR = R * 1.01  // slightly above main sphere

    puzzleRings.forEach((ring, ringI) => {
      const segAngle = (Math.PI * 2) / ring.segs
      for (let s = 0; s < ring.segs; s++) {
        const theta0 = s * segAngle + gapAngle / 2
        const theta1 = (s + 1) * segAngle - gapAngle / 2
        const phi0 = ring.phi - ring.dPhi / 2
        const phi1 = ring.phi + ring.dPhi / 2

        // Build spherical quad mesh
        const positions = []
        const indices = []
        for (let pi = 0; pi <= phiRes; pi++) {
          for (let ti = 0; ti <= arcRes; ti++) {
            const phi = phi0 + (pi / phiRes) * (phi1 - phi0)
            const theta = theta0 + (ti / arcRes) * (theta1 - theta0)
            positions.push(
              puzzleR * Math.sin(phi) * Math.cos(theta),
              puzzleR * Math.cos(phi),
              puzzleR * Math.sin(phi) * Math.sin(theta)
            )
          }
        }
        for (let pi = 0; pi < phiRes; pi++) {
          for (let ti = 0; ti < arcRes; ti++) {
            const a = pi * (arcRes + 1) + ti
            const b = a + 1
            const c = a + arcRes + 1
            const d = c + 1
            indices.push(a, c, b, b, c, d)
          }
        }

        const geo = new THREE.BufferGeometry()
        geo.setAttribute('position', new THREE.Float32BufferAttribute(positions, 3))
        geo.setIndex(indices)
        geo.computeVertexNormals()

        // Edge outline on sphere surface
        const edgePts = []
        // Top arc (outer phi)
        for (let ti = 0; ti <= arcRes; ti++) {
          const theta = theta0 + (ti / arcRes) * (theta1 - theta0)
          edgePts.push(new THREE.Vector3(
            puzzleR * Math.sin(phi0) * Math.cos(theta),
            puzzleR * Math.cos(phi0),
            puzzleR * Math.sin(phi0) * Math.sin(theta)
          ))
        }
        // Right side
        for (let pi = 0; pi <= phiRes; pi++) {
          const phi = phi0 + (pi / phiRes) * (phi1 - phi0)
          edgePts.push(new THREE.Vector3(
            puzzleR * Math.sin(phi) * Math.cos(theta1),
            puzzleR * Math.cos(phi),
            puzzleR * Math.sin(phi) * Math.sin(theta1)
          ))
        }
        // Bottom arc (reverse)
        for (let ti = arcRes; ti >= 0; ti--) {
          const theta = theta0 + (ti / arcRes) * (theta1 - theta0)
          edgePts.push(new THREE.Vector3(
            puzzleR * Math.sin(phi1) * Math.cos(theta),
            puzzleR * Math.cos(phi1),
            puzzleR * Math.sin(phi1) * Math.sin(theta)
          ))
        }
        // Left side
        for (let pi = phiRes; pi >= 0; pi--) {
          const phi = phi0 + (pi / phiRes) * (phi1 - phi0)
          edgePts.push(new THREE.Vector3(
            puzzleR * Math.sin(phi) * Math.cos(theta0),
            puzzleR * Math.cos(phi),
            puzzleR * Math.sin(phi) * Math.sin(theta0)
          ))
        }
        edgePts.push(edgePts[0].clone())

        const edgeMat = new THREE.LineBasicMaterial({
          color: glowInt, transparent: true, opacity: 0.45,
          blending: THREE.AdditiveBlending,
        })
        themedMaterials.push({ mat: edgeMat, role: 'glow' })
        const edgeLine = new THREE.Line(
          new THREE.BufferGeometry().setFromPoints(edgePts),
          edgeMat
        )

        const mat = new THREE.MeshBasicMaterial({
          color: primaryInt,
          transparent: true,
          opacity: 0.08,
          side: THREE.DoubleSide,
          blending: THREE.AdditiveBlending,
          depthWrite: false,
        })
        themedMaterials.push({ mat, role: 'glow' })

        const piece = new THREE.Group()
        piece.add(new THREE.Mesh(geo, mat))
        piece.add(edgeLine)

        // Outward normal for this piece (center of the cell)
        const midPhi = (phi0 + phi1) / 2
        const midTheta = (theta0 + theta1) / 2
        const normal = new THREE.Vector3(
          Math.sin(midPhi) * Math.cos(midTheta),
          Math.cos(midPhi),
          Math.sin(midPhi) * Math.sin(midTheta)
        ).normalize()

        piece.userData.phase = Math.random() * Math.PI * 2
        piece.userData.ringIdx = ringI
        piece.userData.normal = normal
        piece.userData.mat = mat

        globe.add(piece)
        cellMeshes.push(piece)
      }
    })

    // ── 4. Random "circuit" lines on surface (the city-grid look) ──
    for (let i = 0; i < 80; i++) {
      const pts = []
      let theta = Math.random() * Math.PI * 2
      let phi = Math.acos(2 * Math.random() - 1)
      const steps = 3 + Math.floor(Math.random() * 8)
      const layer = R * (0.85 + Math.random() * 0.2)

      for (let j = 0; j < steps; j++) {
        pts.push(new THREE.Vector3(
          layer * Math.sin(phi) * Math.cos(theta),
          layer * Math.cos(phi),
          layer * Math.sin(phi) * Math.sin(theta)
        ))
        // Random walk on sphere surface
        if (Math.random() > 0.5) {
          theta += (Math.random() - 0.5) * 0.3
        } else {
          phi += (Math.random() - 0.5) * 0.2
          phi = Math.max(0.1, Math.min(Math.PI - 0.1, phi))
        }
      }
      globe.add(new THREE.Line(
        new THREE.BufferGeometry().setFromPoints(pts),
        lineMat(0.3 + Math.random() * 0.15)
      ))
    }

    // ── 5. Energy lines from core to surface ──
    for (let i = 0; i < 20; i++) {
      const theta = Math.random() * Math.PI * 2
      const phi = Math.acos(2 * Math.random() - 1)
      const pts = []
      for (let j = 0; j < 6; j++) {
        const t = j / 5
        const r = t * R
        pts.push(new THREE.Vector3(
          r * Math.sin(phi) * Math.cos(theta),
          r * Math.cos(phi),
          r * Math.sin(phi) * Math.sin(theta)
        ))
      }
      globe.add(new THREE.Line(new THREE.BufferGeometry().setFromPoints(pts), lineMat(0.3 + Math.random() * 0.15)))
    }

    // ── 6. Bright orbital bands (3D wobble, tilted) ──
    const bands = []
    for (let b = 0; b < 5; b++) {
      const bandR = R + 0.02 + b * 0.01
      const pts = []
      for (let j = 0; j <= 128; j++) {
        const a = (j / 128) * Math.PI * 2
        // Wobble in Y for 3D depth instead of flat circle
        const wobbleY = 0.04 * Math.sin(a * (2 + b) + b * 0.7)
        pts.push(new THREE.Vector3(bandR * Math.cos(a), wobbleY, bandR * Math.sin(a)))
      }
      const band = new THREE.Line(
        new THREE.BufferGeometry().setFromPoints(pts),
        lineMat(0.3 + Math.random() * 0.2)
      )
      band.rotation.x = b * 0.45 + Math.random() * 0.3
      band.rotation.y = b * 0.2
      band.rotation.z = b * 0.35 - 0.3
      bands.push(band)
      globe.add(band)
    }

    // ── 7. Central rings (the "eye") — real 3D torus geometry ──
    const eyeRings = []
    const eyeConfigs = [
      { radius: 0.20, tube: 0.008, tilt: { x: 0.3,  y: 0,    z: 0.2  } },
      { radius: 0.28, tube: 0.010, tilt: { x: -0.4, y: 0.5,  z: -0.1 } },
      { radius: 0.36, tube: 0.012, tilt: { x: 0.15, y: -0.3, z: 0.4  } },
      { radius: 0.44, tube: 0.010, tilt: { x: -0.2, y: 0.4,  z: -0.3 } },
      { radius: 0.52, tube: 0.008, tilt: { x: 0.5,  y: -0.2, z: 0.15 } },
    ]
    eyeConfigs.forEach((cfg, i) => {
      const torusGeo = new THREE.TorusGeometry(cfg.radius, cfg.tube, 8, 64)
      const torusMat = new THREE.MeshBasicMaterial({
        color: glowInt,
        transparent: true,
        opacity: 0.4 + i * 0.04,
        blending: THREE.AdditiveBlending,
        depthWrite: false,
        side: THREE.DoubleSide,
      })
      themedMaterials.push({ mat: torusMat, role: 'glow' })
      const ring = new THREE.Mesh(torusGeo, torusMat)
      ring.rotation.x = cfg.tilt.x
      ring.rotation.y = cfg.tilt.y
      ring.rotation.z = cfg.tilt.z
      globe.add(ring)
      eyeRings.push(ring)
    })

    // ── 8. Surface particles (accent sparkle) ──
    const SPARK_N = 5000
    const sparkPos = new Float32Array(SPARK_N * 3)
    const sparkPhases = new Float32Array(SPARK_N)
    const sparkBase = new Float32Array(SPARK_N * 3)
    const sparkNormals = new Float32Array(SPARK_N * 3)

    for (let i = 0; i < SPARK_N; i++) {
      const theta = Math.random() * Math.PI * 2
      const phi = Math.acos(2 * Math.random() - 1)
      const r = R * (0.4 + Math.random() * 0.8)
      const x = r * Math.sin(phi) * Math.cos(theta)
      const y = r * Math.sin(phi) * Math.sin(theta)
      const z = r * Math.cos(phi)
      const idx = i * 3
      sparkPos[idx] = x; sparkPos[idx + 1] = y; sparkPos[idx + 2] = z
      sparkBase[idx] = x; sparkBase[idx + 1] = y; sparkBase[idx + 2] = z
      sparkNormals[idx] = Math.sin(phi) * Math.cos(theta)
      sparkNormals[idx + 1] = Math.sin(phi) * Math.sin(theta)
      sparkNormals[idx + 2] = Math.cos(phi)
      sparkPhases[i] = Math.random() * Math.PI * 2
    }

    const sparkGeo = new THREE.BufferGeometry()
    sparkGeo.setAttribute('position', new THREE.BufferAttribute(sparkPos, 3))
    const sparkMat = new THREE.PointsMaterial({
      size: 0.012,
      color: primaryInt,
      transparent: true,
      opacity: 0.5,
      blending: THREE.AdditiveBlending, depthWrite: false,
    })
    globe.add(new THREE.Points(sparkGeo, sparkMat))

    // ── 8b. Dense cyan neural lines — bright crisscrossing web inside sphere ──
    const NEURAL_LINES = 80
    const signals = []
    for (let i = 0; i < NEURAL_LINES; i++) {
      const tiltX = Math.random() * Math.PI
      const tiltZ = Math.random() * Math.PI
      const tiltY = Math.random() * Math.PI * 0.5
      const orbitR = R * (0.3 + Math.random() * 0.65)
      const speed = (1.0 + Math.random() * 2.0) * (Math.random() > 0.5 ? 1 : -1)
      // Each line is a partial arc (not full circle)
      const arcLen = 0.4 + Math.random() * 1.2  // radians of arc
      const segs = 20 + Math.floor(Math.random() * 20)

      const positions = new Float32Array(segs * 3)
      const geo = new THREE.BufferGeometry()
      geo.setAttribute('position', new THREE.BufferAttribute(positions, 3))

      const brightness = 0.15 + Math.random() * 0.35
      const neuralColor = i % 5 === 0 ? glowInt : i % 3 === 0 ? primaryInt : structInt
      const mat = new THREE.LineBasicMaterial({
        color: neuralColor,
        transparent: true,
        opacity: brightness,
        blending: THREE.AdditiveBlending,
        depthWrite: false,
      })
      themedMaterials.push({ mat, role: i % 5 === 0 ? 'glow' : 'struct' })
      const line = new THREE.Line(geo, mat)
      globe.add(line)

      signals.push({
        angle: Math.random() * Math.PI * 2,
        tiltX, tiltZ, tiltY, orbitR, speed, arcLen, segs,
        positions, geo, mat, baseBrightness: brightness,
      })
    }

    // ── 9. Glowing light point in the center ──
    const glowTex = (() => {
      const size = 128
      const c = document.createElement('canvas')
      c.width = size; c.height = size
      const ctx = c.getContext('2d')
      const grad = ctx.createRadialGradient(size/2, size/2, 0, size/2, size/2, size/2)
      const gr = (glowInt >> 16) & 0xff, gg = (glowInt >> 8) & 0xff, gb = glowInt & 0xff
      grad.addColorStop(0, `rgba(${gr}, ${gg}, ${gb}, 1)`)
      grad.addColorStop(0.15, `rgba(${pr}, ${pg}, ${pb}, 0.8)`)
      grad.addColorStop(0.4, `rgba(${pr}, ${pg}, ${pb}, 0.3)`)
      grad.addColorStop(1, `rgba(${pr}, ${pg}, ${pb}, 0)`)
      ctx.fillStyle = grad
      ctx.fillRect(0, 0, size, size)
      const tex = new THREE.CanvasTexture(c)
      return tex
    })()

    const glowMat = new THREE.SpriteMaterial({
      map: glowTex,
      color: primaryInt,
      transparent: true,
      opacity: 0.7,
      blending: THREE.AdditiveBlending,
      depthWrite: false,
    })
    const glowSprite = new THREE.Sprite(glowMat)
    glowSprite.scale.set(3.5, 3.5, 1)
    globe.add(glowSprite)

    // ── Animate ──
    let time = 0, smoothAudio = 0, animId, lastFrame = 0
    const TARGET_FPS = 30
    const FRAME_MS  = 1000 / TARGET_FPS

    function animate(now = 0) {
      animId = requestAnimationFrame(animate)
      if (now - lastFrame < FRAME_MS) return
      lastFrame = now
      time += 0.006  // doubled step to compensate for halved call rate
      const audio = audioRef.current
      const st = stateRef.current

      const raw = THREE.MathUtils.clamp(audio * 5, 0, 1)
      // Fast attack, slow decay — snappy pulse response
      const smoothFactor = raw > smoothAudio ? 0.3 : 0.08
      smoothAudio += (raw - smoothAudio) * smoothFactor

      // Simulate audio pulse when speaking (TTS doesn't feed audioLevel)
      const speakPulse = st === 'speaking'
        ? 0.4 + 0.4 * Math.sin(time * 8.0) * Math.sin(time * 3.0)
        : 0
      const energy = Math.max(smoothAudio, speakPulse)

      // ── State-driven color transitions ──
      // listening/idle = green  | thinking = amber  | speaking = blue  | offline = red
      let targetColor = primaryInt
      let targetGlow = glowInt
      if (st === 'offline') {
        targetColor = 0xef4444  // red-500  — disconnected
        targetGlow = 0xf87171   // red-400
      } else if (st === 'thinking') {
        targetColor = 0xf59e0b  // amber-500 — processing
        targetGlow = 0xfbbf24   // amber-400
      } else if (st === 'speaking') {
        targetColor = 0x3b82f6  // blue-500  — JARVIS talking
        targetGlow = 0x60a5fa   // blue-400
      } else if (st === 'idle' || st === 'listening' || st === 'ready') {
        targetColor = 0x22c55e  // green-500 — listening / ready
        targetGlow = 0x4ade80   // green-400
      } else if (st === 'booting') {
        targetColor = 0xf59e0b  // amber-500 — initializing
        targetGlow = 0xfbbf24   // amber-400
      }

      // Smooth color lerp on all themed materials
      const lerpColor = (current, target, t) => {
        const cr = (current >> 16) & 0xff, cg = (current >> 8) & 0xff, cb = current & 0xff
        const tr = (target >> 16) & 0xff, tg = (target >> 8) & 0xff, tb = target & 0xff
        const r = Math.round(cr + (tr - cr) * t)
        const g = Math.round(cg + (tg - cg) * t)
        const b = Math.round(cb + (tb - cb) * t)
        return (r << 16) | (g << 8) | b
      }

      const colorLerp = st === 'ready' ? 0.15 : 0.08  // faster transition for ready flash
      for (const tm of themedMaterials) {
        const curInt = tm.mat.color.getHex()
        const tgt = tm.role === 'glow' ? targetGlow : targetColor
        if (curInt !== tgt) {
          tm.mat.color.setHex(lerpColor(curInt, tgt, colorLerp))
        }
      }
      sparkMat.color.setHex(lerpColor(sparkMat.color.getHex(), targetGlow, colorLerp))
      glowMat.color.setHex(lerpColor(glowMat.color.getHex(), targetGlow, colorLerp))

      // Ready state: boost glow intensity
      if (st === 'ready') {
        glowMat.opacity = Math.min(1.0, glowMat.opacity + 0.02)
        const gs = 4.5 + 1.5 * Math.sin(time * 3.0)
        glowSprite.scale.set(gs, gs, 1)
      }

      // Globe rotation
      const speed = st === 'thinking' ? 0.005 : st === 'speaking' ? 0.003 : st === 'ready' ? 0.008 : 0.0015
      globe.rotation.y += speed
      globe.rotation.x = Math.sin(time * 0.3) * 0.08

      // Bands rotate
      bands.forEach((b, i) => {
        b.rotation.y += (0.003 + i * 0.0015) * (i % 2 === 0 ? 1 : -1)
      })

      // Eye rings — slow independent rotation + status color indicator
      // green = listening | amber = thinking | blue = speaking | red = offline
      const eyeTargetColor = st === 'offline'    ? 0xf87171   // red-400
        : st === 'thinking'                       ? 0xfbbf24   // amber-400
        : st === 'speaking'                       ? 0x60a5fa   // blue-400
        : st === 'idle' || st === 'listening' || st === 'ready' ? 0x4ade80  // green-400
        : st === 'booting'                        ? 0xfbbf24   // amber-400
        : glowInt
      const eyeLerp = st === 'speaking' ? 0.15 : st === 'thinking' ? 0.12 : st === 'offline' ? 0.2 : 0.06
      eyeRings.forEach((ring, i) => {
        ring.rotation.x += (0.002 + i * 0.001) * (i % 2 === 0 ? 1 : -1)
        ring.rotation.z += 0.001 * (i % 2 === 0 ? -1 : 1)
        // Color transition
        const curHex = ring.material.color.getHex()
        if (curHex !== eyeTargetColor) {
          ring.material.color.setHex(lerpColor(curHex, eyeTargetColor, eyeLerp))
        }
        // State-driven brightness
        if (st === 'speaking') {
          ring.material.opacity = 0.5 + 0.4 * Math.sin(time * 5 + i * 1.2)
        } else if (st === 'thinking') {
          ring.material.opacity = 0.4 + 0.4 * Math.sin(time * 3 + i * 0.8)
        } else if (st === 'idle' || st === 'listening' || st === 'ready') {
          ring.material.opacity = 0.5 + 0.25 * Math.sin(time * 2 + i * 1.5)
        } else if (st === 'offline') {
          ring.material.opacity = 0.2 + 0.15 * Math.sin(time * 1.5 + i)
        } else if (st === 'booting') {
          ring.material.opacity = 0.3 + 0.2 * Math.sin(time * 2 + i)
        }
      })

      // Neural lines — dense cyan arcs racing inside sphere
      const sigSpeed = st === 'thinking' ? 5.0 : st === 'speaking' ? 3.5 : 2.5
      for (let s = 0; s < signals.length; s++) {
        const sig = signals[s]
        sig.angle += sig.speed * 0.012 * sigSpeed
        const cosX = Math.cos(sig.tiltX), sinX = Math.sin(sig.tiltX)
        const cosZ = Math.cos(sig.tiltZ), sinZ = Math.sin(sig.tiltZ)
        const cosY = Math.cos(sig.tiltY), sinY = Math.sin(sig.tiltY)

        for (let t = 0; t < sig.segs; t++) {
          const a = sig.angle + (t / sig.segs) * sig.arcLen
          let x = sig.orbitR * Math.cos(a)
          let y = 0
          let z = sig.orbitR * Math.sin(a)
          // Tilt X
          let y2 = y * cosX - z * sinX
          let z2 = y * sinX + z * cosX
          // Tilt Z
          let x2 = x * cosZ - y2 * sinZ
          let y3 = x * sinZ + y2 * cosZ
          // Tilt Y
          let x3 = x2 * cosY + z2 * sinY
          let z3 = -x2 * sinY + z2 * cosY

          const idx = t * 3
          sig.positions[idx] = x3
          sig.positions[idx + 1] = y3
          sig.positions[idx + 2] = z3
        }
        sig.geo.attributes.position.needsUpdate = true
        sig.mat.opacity = sig.baseBrightness + energy * 0.3 + 0.1 * Math.sin(time * 4.0 + s)
      }

      // Continuous breathing (always active, no audio needed)
      const breathe = 0.5 + 0.5 * Math.sin(time * 1.5)
      const breatheFast = 0.5 + 0.5 * Math.sin(time * 4.0)

      // Particles displace outward with breathing + audio
      const arr = sparkGeo.attributes.position.array
      const disp = 0.06 * breathe + 0.04 * breatheFast + energy * 0.6
      for (let i = 0; i < SPARK_N; i++) {
        const idx = i * 3
        const off = disp * Math.sin(time * 3 + sparkPhases[i])
        arr[idx] = sparkBase[idx] + sparkNormals[idx] * off
        arr[idx + 1] = sparkBase[idx + 1] + sparkNormals[idx + 1] * off
        arr[idx + 2] = sparkBase[idx + 2] + sparkNormals[idx + 2] * off
      }
      sparkGeo.attributes.position.needsUpdate = true

      // Puzzle pieces pop in and out along sphere normals
      for (let c = 0; c < cellMeshes.length; c++) {
        const piece = cellMeshes[c]
        const { phase, ringIdx, normal } = piece.userData
        // Ripple wave across the sphere — ring by ring with phase offset
        const wave = Math.sin(time * 2.5 - ringIdx * 0.7 + phase)
        const waveFast = Math.sin(time * 5.0 + phase * 2.0)
        // Push outward along the piece's radial normal
        const push = Math.max(0, 0.1 * wave + 0.04 * waveFast + energy * 0.2)
        piece.position.set(normal.x * push, normal.y * push, normal.z * push)
        // Brighter when popped out
        const bright = 0.05 + 0.15 * Math.max(0, wave) + energy * 0.12
        piece.userData.mat.opacity = bright
      }

      // Particle brightness breathing
      sparkMat.opacity = 0.25 + 0.25 * breathe + energy * 0.5
      sparkMat.size = 0.01 + 0.008 * breathe + energy * 0.006

      // Globe scale breathing
      const sc = 1.0 + 0.03 * Math.sin(time * 1.5) + energy * 0.08
      globe.scale.set(sc, sc, sc)

      // Center glow breathing
      const glowPulse = 0.4 + 0.35 * breathe + energy * 0.3
      glowMat.opacity = glowPulse
      const gs = 3.0 + 1.0 * breathe + energy * 0.8
      glowSprite.scale.set(gs, gs, 1)

      renderer.render(scene, camera)
    }
    animate()

    const onResize = () => {
      camera.aspect = window.innerWidth / window.innerHeight
      camera.updateProjectionMatrix()
      renderer.setSize(window.innerWidth, window.innerHeight)
    }
    window.addEventListener('resize', onResize)

    return () => {
      window.removeEventListener('resize', onResize)
      cancelAnimationFrame(animId)
      renderer.dispose()
      if (el.contains(renderer.domElement)) el.removeChild(renderer.domElement)
    }
  }, [isDesktop, theme])

  return (
    <div ref={mountRef} style={{
      position: 'fixed', inset: 0, width: '100vw', height: '100vh',
      pointerEvents: 'none', zIndex: 1,
    }} />
  )
}
