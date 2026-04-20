// Vanilla-JS port of src/desktop-tauri/src/components/ArcReactor.jsx
// — same JARVIS holographic sphere the desktop Tauri app renders.
// Exposes window.setJarvisState(state, audioLevel) + window.setJarvisAudio(level).

(function () {
  // Shared state updated from outside by the HUD event handler.
  let _state = 'idle';
  let _audioLevel = 0;
  window.setJarvisState = function (state, audioLevel) {
    _state = state;
    if (typeof audioLevel === 'number') _audioLevel = audioLevel;
  };
  window.setJarvisAudio = function (level) { _audioLevel = level || 0; };

  // Detect WebGL; if unavailable or broken (common in VMware without GPU),
  // render a 2D-canvas fallback that looks like a simpler reactor.
  function hasWebGL() {
    try {
      const c = document.createElement('canvas');
      return !!(c.getContext('webgl2') || c.getContext('webgl') || c.getContext('experimental-webgl'));
    } catch { return false; }
  }

  if (typeof THREE === 'undefined' || !hasWebGL()) {
    renderFallback2D();
    return;
  }

  function renderFallback2D() {
    const canvas = document.createElement('canvas');
    canvas.style.cssText = 'position:fixed;inset:0;width:100%;height:100%;z-index:0;pointer-events:none;background:transparent;';
    document.body.appendChild(canvas);
    const ctx = canvas.getContext('2d');

    function resize() { canvas.width = window.innerWidth; canvas.height = window.innerHeight; }
    resize(); window.addEventListener('resize', resize);

    const stateColor = {
      offline:   '#f87171',
      thinking:  '#fbbf24',
      booting:   '#334455',
      speaking:  '#67e8f9',
      listening: '#a78bfa',
      ready:     '#7ecf85',
      idle:      '#00e5ff',
    };

    let t = 0, smoothAudio = 0;
    function frame() {
      requestAnimationFrame(frame);
      t += 0.016;
      const st = _state, audio = _audioLevel;
      const color = stateColor[st] || '#00e5ff';
      const raw = Math.min(1, Math.max(0, audio * 5));
      const sf = raw > smoothAudio ? 0.3 : 0.08;
      smoothAudio += (raw - smoothAudio) * sf;
      const speakPulse = st === 'speaking' ? 0.4 + 0.4 * Math.sin(t * 8) * Math.sin(t * 3) : 0;
      const energy = Math.max(smoothAudio, speakPulse);

      const cx = canvas.width / 2, cy = canvas.height / 2;
      const baseR = Math.min(canvas.width, canvas.height) * 0.15;
      ctx.clearRect(0, 0, canvas.width, canvas.height);

      // Outer halo
      const haloR = baseR * (1.8 + energy * 0.6 + 0.08 * Math.sin(t * 1.5));
      const halo = ctx.createRadialGradient(cx, cy, baseR * 0.2, cx, cy, haloR);
      halo.addColorStop(0, color + 'cc');
      halo.addColorStop(0.4, color + '66');
      halo.addColorStop(1, color + '00');
      ctx.fillStyle = halo; ctx.beginPath(); ctx.arc(cx, cy, haloR, 0, Math.PI * 2); ctx.fill();

      // Orbital rings — 5 concentric, tilted slightly, rotating
      ctx.lineWidth = 1.5;
      for (let i = 0; i < 5; i++) {
        const r = baseR * (0.4 + i * 0.22) * (1 + energy * 0.1);
        const rot = t * (0.2 + i * 0.05) * (i % 2 === 0 ? 1 : -1);
        const alpha = 0.35 + 0.25 * Math.sin(t * (2 + i) + i * 1.2) + energy * 0.2;
        ctx.strokeStyle = color + Math.floor(Math.min(1, alpha) * 255).toString(16).padStart(2, '0');
        ctx.beginPath();
        ctx.ellipse(cx, cy, r, r * (0.25 + 0.2 * Math.sin(rot * 2)), rot, 0, Math.PI * 2);
        ctx.stroke();
      }

      // Core disc
      const coreR = baseR * (0.35 + energy * 0.15 + 0.04 * Math.sin(t * 4));
      const core = ctx.createRadialGradient(cx, cy, 0, cx, cy, coreR);
      core.addColorStop(0, '#ffffff');
      core.addColorStop(0.3, color);
      core.addColorStop(1, color + '00');
      ctx.fillStyle = core; ctx.beginPath(); ctx.arc(cx, cy, coreR, 0, Math.PI * 2); ctx.fill();

      // Sparks rotating around
      const sparkN = 32;
      for (let i = 0; i < sparkN; i++) {
        const a = (i / sparkN) * Math.PI * 2 + t * 0.5;
        const rr = baseR * (1.0 + 0.4 * Math.sin(t * 2 + i));
        const sx = cx + Math.cos(a) * rr, sy = cy + Math.sin(a) * rr * 0.85;
        ctx.fillStyle = color + Math.floor((0.5 + 0.5 * Math.sin(t * 3 + i)) * 200).toString(16).padStart(2, '0');
        ctx.beginPath(); ctx.arc(sx, sy, 1.5 + energy * 2, 0, Math.PI * 2); ctx.fill();
      }
    }
    frame();
  }

  // Ghost silver — pale white-blue like the Age-of-Ultron JARVIS reactor.
  // State colors (listening purple, thinking amber, etc.) still override this
  // at runtime via lerp; this is the idle/default hue.
  const theme = { primary: '#c4d8e8', glow: '#e8f4ff' };
  const hexToInt = (h) => parseInt(h.replace('#', ''), 16);
  const primaryInt = hexToInt(theme.primary);
  const glowInt = hexToInt(theme.glow);
  const pr = (primaryInt >> 16) & 0xff, pg = (primaryInt >> 8) & 0xff, pb = primaryInt & 0xff;
  const structInt = ((Math.min(255, pr + 60) << 16) | (Math.min(255, pg + 60) << 8) | Math.min(255, pb + 60));

  const scene = new THREE.Scene();
  const camera = new THREE.PerspectiveCamera(50, window.innerWidth / window.innerHeight, 0.1, 200);
  const targetSphereHeight = 280;
  const sphereFraction = 0.22;
  const getCamZ = () => 10 * (window.innerHeight * sphereFraction / targetSphereHeight);
  camera.position.z = getCamZ();
  camera.lookAt(0, 0, 0);

  const renderer = new THREE.WebGLRenderer({ alpha: true, antialias: true, premultipliedAlpha: false });
  renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
  renderer.setSize(window.innerWidth, window.innerHeight);
  renderer.setClearColor(0x000000, 0);
  renderer.domElement.style.cssText = 'position:fixed;inset:0;width:100%;height:100%;background:transparent;pointer-events:none;';
  document.body.appendChild(renderer.domElement);

  const globe = new THREE.Group();
  scene.add(globe);
  const R = 1.3;

  // Glass sphere — fresnel rim
  const glassGeo = new THREE.SphereGeometry(R * 1.01, 64, 64);
  const glassMat = new THREE.ShaderMaterial({
    uniforms: {
      rimColor: { value: new THREE.Color(glowInt) },
      // Wider rim (lower power) + higher intensity makes the silhouette glow
      // brighter, giving a much stronger sense of spherical volume.
      rimPower: { value: 2.0 },
      rimIntensity: { value: 2.2 },
    },
    transparent: true, depthWrite: false, depthTest: false,
    side: THREE.DoubleSide, blending: THREE.AdditiveBlending,
    vertexShader: 'varying vec3 vNormal; varying vec3 vViewDir; void main() { vec4 mvPosition = modelViewMatrix * vec4(position, 1.0); vNormal = normalize(normalMatrix * normal); vViewDir = normalize(-mvPosition.xyz); gl_Position = projectionMatrix * mvPosition; }',
    fragmentShader: 'uniform vec3 rimColor; uniform float rimPower; uniform float rimIntensity; varying vec3 vNormal; varying vec3 vViewDir; void main() { float fres = pow(1.0 - abs(dot(vNormal, vViewDir)), rimPower); vec3 col = rimColor * fres * rimIntensity; gl_FragColor = vec4(col, fres); }',
  });
  globe.add(new THREE.Mesh(glassGeo, glassMat));

  // Materials that should tint with phase color in the animate loop.
  const themedMats = [];
  const lineMat = (opacity) => {
    const m = new THREE.LineBasicMaterial({
      color: glowInt, transparent: true, opacity, blending: THREE.AdditiveBlending, depthWrite: false, linewidth: 1.5,
    });
    themedMats.push(m);
    return m;
  };

  // Puzzle pieces
  const cellMeshes = [];
  const puzzleRings = [
    { phi: 0.30, segs: 4 }, { phi: 0.55, segs: 6 }, { phi: 0.80, segs: 8 }, { phi: 1.05, segs: 10 }, { phi: 1.30, segs: 12 },
    { phi: 1.55, segs: 14 }, { phi: 1.80, segs: 10 }, { phi: 2.05, segs: 8 }, { phi: 2.30, segs: 6 }, { phi: 2.55, segs: 4 },
  ].map(r => ({ ...r, dPhi: 0.18 }));
  const gapAngle = 0.08, arcRes = 6, phiRes = 3, puzzleR = R * 1.01;
  puzzleRings.forEach((ring, ringI) => {
    const segAngle = (Math.PI * 2) / ring.segs;
    for (let s = 0; s < ring.segs; s++) {
      const theta0 = s * segAngle + gapAngle / 2, theta1 = (s + 1) * segAngle - gapAngle / 2;
      const phi0 = ring.phi - ring.dPhi / 2, phi1 = ring.phi + ring.dPhi / 2;
      const positions = [], indices = [];
      for (let pi = 0; pi <= phiRes; pi++) for (let ti = 0; ti <= arcRes; ti++) {
        const phi = phi0 + (pi / phiRes) * (phi1 - phi0), theta = theta0 + (ti / arcRes) * (theta1 - theta0);
        positions.push(puzzleR * Math.sin(phi) * Math.cos(theta), puzzleR * Math.cos(phi), puzzleR * Math.sin(phi) * Math.sin(theta));
      }
      for (let pi = 0; pi < phiRes; pi++) for (let ti = 0; ti < arcRes; ti++) {
        const a = pi * (arcRes + 1) + ti, b = a + 1, c = a + arcRes + 1, d = c + 1;
        indices.push(a, c, b, b, c, d);
      }
      const geo = new THREE.BufferGeometry();
      geo.setAttribute('position', new THREE.Float32BufferAttribute(positions, 3));
      geo.setIndex(indices);
      geo.computeVertexNormals();

      const edgePts = [];
      for (let ti = 0; ti <= arcRes; ti++) { const theta = theta0 + (ti / arcRes) * (theta1 - theta0); edgePts.push(new THREE.Vector3(puzzleR * Math.sin(phi0) * Math.cos(theta), puzzleR * Math.cos(phi0), puzzleR * Math.sin(phi0) * Math.sin(theta))); }
      for (let pi = 0; pi <= phiRes; pi++) { const phi = phi0 + (pi / phiRes) * (phi1 - phi0); edgePts.push(new THREE.Vector3(puzzleR * Math.sin(phi) * Math.cos(theta1), puzzleR * Math.cos(phi), puzzleR * Math.sin(phi) * Math.sin(theta1))); }
      for (let ti = arcRes; ti >= 0; ti--) { const theta = theta0 + (ti / arcRes) * (theta1 - theta0); edgePts.push(new THREE.Vector3(puzzleR * Math.sin(phi1) * Math.cos(theta), puzzleR * Math.cos(phi1), puzzleR * Math.sin(phi1) * Math.sin(theta))); }
      for (let pi = phiRes; pi >= 0; pi--) { const phi = phi0 + (pi / phiRes) * (phi1 - phi0); edgePts.push(new THREE.Vector3(puzzleR * Math.sin(phi) * Math.cos(theta0), puzzleR * Math.cos(phi), puzzleR * Math.sin(phi) * Math.sin(theta0))); }
      edgePts.push(edgePts[0].clone());
      const edgeMat = new THREE.LineBasicMaterial({ color: glowInt, transparent: true, opacity: 0.75, blending: THREE.AdditiveBlending });
      themedMats.push(edgeMat);
      globe.add(new THREE.Line(new THREE.BufferGeometry().setFromPoints(edgePts), edgeMat));

      const mat = new THREE.MeshBasicMaterial({ color: primaryInt, transparent: true, opacity: 0.08, side: THREE.DoubleSide, blending: THREE.AdditiveBlending, depthWrite: false });
      themedMats.push(mat);
      const piece = new THREE.Group();
      piece.add(new THREE.Mesh(geo, mat));
      const midPhi = (phi0 + phi1) / 2, midTheta = (theta0 + theta1) / 2;
      const normal = new THREE.Vector3(Math.sin(midPhi) * Math.cos(midTheta), Math.cos(midPhi), Math.sin(midPhi) * Math.sin(midTheta)).normalize();
      piece.userData = { phase: Math.random() * Math.PI * 2, ringIdx: ringI, normal, mat };
      globe.add(piece); cellMeshes.push(piece);
    }
  });

  // Circuit lines
  for (let i = 0; i < 80; i++) {
    const pts = []; let theta = Math.random() * Math.PI * 2, phi = Math.acos(2 * Math.random() - 1);
    const steps = 3 + Math.floor(Math.random() * 8), layer = R * (0.85 + Math.random() * 0.2);
    for (let j = 0; j < steps; j++) {
      pts.push(new THREE.Vector3(layer * Math.sin(phi) * Math.cos(theta), layer * Math.cos(phi), layer * Math.sin(phi) * Math.sin(theta)));
      if (Math.random() > 0.5) theta += (Math.random() - 0.5) * 0.3;
      else { phi += (Math.random() - 0.5) * 0.2; phi = Math.max(0.1, Math.min(Math.PI - 0.1, phi)); }
    }
    globe.add(new THREE.Line(new THREE.BufferGeometry().setFromPoints(pts), lineMat(0.3 + Math.random() * 0.15)));
  }

  // Energy lines
  for (let i = 0; i < 20; i++) {
    const theta = Math.random() * Math.PI * 2, phi = Math.acos(2 * Math.random() - 1), pts = [];
    for (let j = 0; j < 6; j++) { const r = (j / 5) * R; pts.push(new THREE.Vector3(r * Math.sin(phi) * Math.cos(theta), r * Math.cos(phi), r * Math.sin(phi) * Math.sin(theta))); }
    globe.add(new THREE.Line(new THREE.BufferGeometry().setFromPoints(pts), lineMat(0.3 + Math.random() * 0.15)));
  }

  // Orbital bands
  const bands = [];
  for (let b = 0; b < 5; b++) {
    const bandR = R + 0.02 + b * 0.01, pts = [];
    for (let j = 0; j <= 128; j++) { const a = (j / 128) * Math.PI * 2, wobbleY = 0.04 * Math.sin(a * (2 + b) + b * 0.7); pts.push(new THREE.Vector3(bandR * Math.cos(a), wobbleY, bandR * Math.sin(a))); }
    const band = new THREE.Line(new THREE.BufferGeometry().setFromPoints(pts), lineMat(0.3 + Math.random() * 0.2));
    band.rotation.x = b * 0.45 + Math.random() * 0.3; band.rotation.y = b * 0.2; band.rotation.z = b * 0.35 - 0.3;
    bands.push(band); globe.add(band);
  }

  // Eye rings
  const eyeRings = [];
  const eyeConfigs = [
    { radius: 0.20, tube: 0.008, tilt: { x: 0.3, y: 0, z: 0.2 } },
    { radius: 0.28, tube: 0.010, tilt: { x: -0.4, y: 0.5, z: -0.1 } },
    { radius: 0.36, tube: 0.012, tilt: { x: 0.15, y: -0.3, z: 0.4 } },
    { radius: 0.44, tube: 0.010, tilt: { x: -0.2, y: 0.4, z: -0.3 } },
    { radius: 0.52, tube: 0.008, tilt: { x: 0.5, y: -0.2, z: 0.15 } },
  ];
  eyeConfigs.forEach((cfg) => {
    const torusMat = new THREE.MeshBasicMaterial({ color: glowInt, transparent: true, opacity: 0, blending: THREE.AdditiveBlending, depthWrite: false, side: THREE.DoubleSide });
    const ring = new THREE.Mesh(new THREE.TorusGeometry(cfg.radius, cfg.tube, 8, 64), torusMat);
    ring.rotation.x = cfg.tilt.x; ring.rotation.y = cfg.tilt.y; ring.rotation.z = cfg.tilt.z;
    globe.add(ring); eyeRings.push(ring);
  });

  // Scan ring
  const scanRingMat = new THREE.MeshBasicMaterial({ color: 0xfbbf24, transparent: true, opacity: 0, blending: THREE.AdditiveBlending, depthWrite: false });
  const scanRing = new THREE.Mesh(new THREE.TorusGeometry(R * 1.05, 0.006, 4, 80), scanRingMat);
  globe.add(scanRing);

  // Sparks
  const SPARK_N = 5000;
  const sparkPos = new Float32Array(SPARK_N * 3), sparkPhases = new Float32Array(SPARK_N), sparkBase = new Float32Array(SPARK_N * 3), sparkNormals = new Float32Array(SPARK_N * 3);
  for (let i = 0; i < SPARK_N; i++) {
    const theta = Math.random() * Math.PI * 2, phi = Math.acos(2 * Math.random() - 1), r = R * (0.4 + Math.random() * 0.8);
    const x = r * Math.sin(phi) * Math.cos(theta), y = r * Math.sin(phi) * Math.sin(theta), z = r * Math.cos(phi), idx = i * 3;
    sparkPos[idx] = x; sparkPos[idx + 1] = y; sparkPos[idx + 2] = z;
    sparkBase[idx] = x; sparkBase[idx + 1] = y; sparkBase[idx + 2] = z;
    sparkNormals[idx] = Math.sin(phi) * Math.cos(theta); sparkNormals[idx + 1] = Math.sin(phi) * Math.sin(theta); sparkNormals[idx + 2] = Math.cos(phi);
    sparkPhases[i] = Math.random() * Math.PI * 2;
  }
  const sparkGeo = new THREE.BufferGeometry(); sparkGeo.setAttribute('position', new THREE.BufferAttribute(sparkPos, 3));
  const sparkMat = new THREE.PointsMaterial({ size: 0.018, color: glowInt, transparent: true, opacity: 0.7, blending: THREE.AdditiveBlending, depthWrite: false, sizeAttenuation: true });
  globe.add(new THREE.Points(sparkGeo, sparkMat));

  // Neural arcs
  const NEURAL_LINES = 80, signals = [];
  for (let i = 0; i < NEURAL_LINES; i++) {
    const tiltX = Math.random() * Math.PI, tiltZ = Math.random() * Math.PI, tiltY = Math.random() * Math.PI * 0.5;
    const orbitR = R * (0.3 + Math.random() * 0.65), speed = (1.0 + Math.random() * 2.0) * (Math.random() > 0.5 ? 1 : -1);
    const arcLen = 0.4 + Math.random() * 1.2, segs = 20 + Math.floor(Math.random() * 20);
    const positions = new Float32Array(segs * 3), geo = new THREE.BufferGeometry();
    geo.setAttribute('position', new THREE.BufferAttribute(positions, 3));
    const brightness = 0.15 + Math.random() * 0.35;
    const neuralColor = i % 5 === 0 ? glowInt : i % 3 === 0 ? primaryInt : structInt;
    const mat = new THREE.LineBasicMaterial({ color: neuralColor, transparent: true, opacity: brightness, blending: THREE.AdditiveBlending, depthWrite: false });
    themedMats.push(mat);
    globe.add(new THREE.Line(geo, mat));
    signals.push({ angle: Math.random() * Math.PI * 2, tiltX, tiltZ, tiltY, orbitR, speed, arcLen, segs, positions, geo, mat, baseBrightness: brightness });
  }

  // Center glow
  const glowTex = (() => {
    const size = 128, c = document.createElement('canvas'); c.width = size; c.height = size;
    const ctx = c.getContext('2d');
    ctx.clearRect(0, 0, size, size); ctx.save();
    ctx.beginPath(); ctx.arc(size / 2, size / 2, size / 2, 0, Math.PI * 2); ctx.clip();
    const grad = ctx.createRadialGradient(size / 2, size / 2, 0, size / 2, size / 2, size / 2);
    const gr = (glowInt >> 16) & 0xff, gg = (glowInt >> 8) & 0xff, gb = glowInt & 0xff;
    grad.addColorStop(0, `rgba(${gr},${gg},${gb},1)`);
    grad.addColorStop(0.15, `rgba(${pr},${pg},${pb},0.8)`);
    grad.addColorStop(0.4, `rgba(${pr},${pg},${pb},0.3)`);
    grad.addColorStop(1, `rgba(${pr},${pg},${pb},0)`);
    ctx.fillStyle = grad; ctx.fillRect(0, 0, size, size); ctx.restore();
    const tex = new THREE.CanvasTexture(c); tex.premultiplyAlpha = true; return tex;
  })();
  const glowMat = new THREE.SpriteMaterial({ map: glowTex, color: glowInt, transparent: true, opacity: 0, blending: THREE.AdditiveBlending, depthWrite: false });
  const glowSprite = new THREE.Sprite(glowMat); glowSprite.scale.set(3.5, 3.5, 1); globe.add(glowSprite);

  // State disc
  const stateTexture = (() => {
    const size = 256, c = document.createElement('canvas'); c.width = size; c.height = size;
    const ctx = c.getContext('2d'), cx = size / 2, cy = size / 2;
    ctx.clearRect(0, 0, size, size); ctx.save();
    ctx.beginPath(); ctx.arc(cx, cy, size / 2, 0, Math.PI * 2); ctx.clip();
    const outer = ctx.createRadialGradient(cx, cy, size * 0.18, cx, cy, size * 0.5);
    outer.addColorStop(0, 'rgba(255,255,255,0)'); outer.addColorStop(0.4, 'rgba(255,255,255,0.55)');
    outer.addColorStop(0.75, 'rgba(255,255,255,0.25)'); outer.addColorStop(1.0, 'rgba(255,255,255,0)');
    ctx.fillStyle = outer; ctx.fillRect(0, 0, size, size);
    const inner = ctx.createRadialGradient(cx, cy, 0, cx, cy, size * 0.22);
    inner.addColorStop(0, 'rgba(255,255,255,1)'); inner.addColorStop(0.5, 'rgba(255,255,255,0.7)'); inner.addColorStop(1.0, 'rgba(255,255,255,0)');
    ctx.fillStyle = inner; ctx.fillRect(0, 0, size, size); ctx.restore();
    return new THREE.CanvasTexture(c);
  })();
  const stateDiscMat = new THREE.SpriteMaterial({ map: stateTexture, color: glowInt, transparent: true, opacity: 0.0, blending: THREE.AdditiveBlending, depthWrite: false });
  const stateDisc = new THREE.Sprite(stateDiscMat); stateDisc.scale.set(1.1, 1.1, 1); stateDisc.position.set(0, 0, 1.35); scene.add(stateDisc);

  // Animation
  let time = 0, smoothAudio = 0, lastFrame = -1;
  const FRAME_MS = 1000 / 30;
  const lerpColor = (current, target, t) => {
    const cr = (current >> 16) & 0xff, cg = (current >> 8) & 0xff, cb = current & 0xff;
    const tr = (target >> 16) & 0xff, tg = (target >> 8) & 0xff, tb = target & 0xff;
    return (Math.round(cr + (tr - cr) * t) << 16) | (Math.round(cg + (tg - cg) * t) << 8) | Math.round(cb + (tb - cb) * t);
  };

  function animate(now) {
    requestAnimationFrame(animate);
    if (lastFrame >= 0 && now - lastFrame < FRAME_MS) return;
    lastFrame = now; time += 0.006;
    const audio = _audioLevel, st = _state;
    const raw = Math.min(1, Math.max(0, audio * 5));
    const sf = raw > smoothAudio ? 0.3 : 0.08;
    smoothAudio += (raw - smoothAudio) * sf;
    const speakPulse = st === 'speaking' ? 0.4 + 0.4 * Math.sin(time * 8.0) * Math.sin(time * 3.0) : 0;
    const energy = Math.max(smoothAudio, speakPulse);

    // Color is locked to ghost silver regardless of state. Pulse speed
    // and opacity still react to voice activity, just not hue.
    const eyeTargetColor = glowInt;
    const eyeLerp = st === 'speaking' ? 0.15 : st === 'thinking' ? 0.12 : st === 'offline' ? 0.2 : st === 'listening' ? 0.10 : 0.06;
    const glowTarget = st === 'booting' ? 0.3 : st === 'ready' ? 1.0 : 0.7;
    glowMat.opacity += (glowTarget - glowMat.opacity) * 0.05;
    glowMat.color.setHex(lerpColor(glowMat.color.getHex(), eyeTargetColor, eyeLerp));
    if (st === 'ready') { const gs = 2.2 + 0.6 * Math.sin(time * 3.0); glowSprite.scale.set(gs, gs, 1); }

    // Constant, more noticeable rotation on both axes so 3D is obvious.
    globe.rotation.y += 0.004;
    globe.rotation.x = Math.sin(time * 0.3) * 0.18;
    globe.rotation.z = Math.cos(time * 0.2) * 0.08;

    if (st === 'thinking') {
      scanRingMat.opacity = Math.min(0.8, scanRingMat.opacity + 0.05);
      scanRing.rotation.y += 0.04; scanRing.rotation.x = Math.sin(time * 1.5) * Math.PI * 0.5;
      scanRing.scale.setScalar(1.0 + 0.05 * Math.sin(time * 6));
    } else {
      scanRingMat.opacity = Math.max(0, scanRingMat.opacity - 0.03);
    }
    bands.forEach((b, i) => { b.rotation.y += (0.003 + i * 0.0015) * (i % 2 === 0 ? 1 : -1); });
    eyeRings.forEach((ring, i) => {
      ring.rotation.x += (0.002 + i * 0.001) * (i % 2 === 0 ? 1 : -1);
      ring.rotation.z += 0.001 * (i % 2 === 0 ? -1 : 1);
      const curHex = ring.material.color.getHex();
      if (curHex !== eyeTargetColor) ring.material.color.setHex(lerpColor(curHex, eyeTargetColor, eyeLerp));
      if (st === 'speaking') ring.material.opacity = 0.5 + 0.4 * Math.sin(time * 5 + i * 1.2);
      else if (st === 'thinking') ring.material.opacity = 0.4 + 0.4 * Math.sin(time * 3 + i * 0.8);
      else if (st === 'listening') ring.material.opacity = 0.55 + 0.30 * Math.sin(time * 4.5 + i * 2.0);
      else if (st === 'idle' || st === 'ready') ring.material.opacity = 0.5 + 0.25 * Math.sin(time * 2 + i * 1.5);
      else if (st === 'offline') ring.material.opacity = 0.2 + 0.15 * Math.sin(time * 1.5 + i);
      else ring.material.opacity = 0.15 + 0.10 * Math.sin(time * 1.0 + i * 1.5);
    });

    stateDiscMat.color.setHex(lerpColor(stateDiscMat.color.getHex(), eyeTargetColor, eyeLerp * 1.5));
    // No color tinting — materials keep their original ghost-silver hue.
    const discBase =
      st === 'offline' ? 0.25 :
      st === 'speaking' ? 0.55 + 0.35 * Math.sin(time * 7 + 0.5) :
      st === 'thinking' ? 0.45 + 0.30 * Math.sin(time * 4) :
      st === 'listening' ? 0.50 + 0.20 * Math.sin(time * 3) :
      st === 'booting' ? 0.15 + 0.08 * Math.sin(time * 1.0) :
      0.40 + 0.15 * Math.sin(time * 1.5);
    stateDiscMat.opacity = Math.min(0.95, Math.max(0, discBase + energy * 0.3));
    const discScale = 1.1 + 0.12 * Math.sin(time * 2) + energy * 0.25;
    stateDisc.scale.set(discScale, discScale, 1);

    const sigSpeed = st === 'thinking' ? 5.0 : st === 'speaking' ? 3.5 : 2.5;
    for (let s = 0; s < signals.length; s++) {
      const sig = signals[s]; sig.angle += sig.speed * 0.012 * sigSpeed;
      const cosX = Math.cos(sig.tiltX), sinX = Math.sin(sig.tiltX);
      const cosZ = Math.cos(sig.tiltZ), sinZ = Math.sin(sig.tiltZ);
      const cosY = Math.cos(sig.tiltY), sinY = Math.sin(sig.tiltY);
      for (let t = 0; t < sig.segs; t++) {
        const a = sig.angle + (t / sig.segs) * sig.arcLen;
        const x = sig.orbitR * Math.cos(a), y = 0, z = sig.orbitR * Math.sin(a);
        const y2 = y * cosX - z * sinX, z2 = y * sinX + z * cosX;
        const x2 = x * cosZ - y2 * sinZ, y3 = x * sinZ + y2 * cosZ;
        const x3 = x2 * cosY + z2 * sinY, z3 = -x2 * sinY + z2 * cosY;
        const idx = t * 3; sig.positions[idx] = x3; sig.positions[idx + 1] = y3; sig.positions[idx + 2] = z3;
      }
      sig.geo.attributes.position.needsUpdate = true;
      sig.mat.opacity = sig.baseBrightness + energy * 0.3 + 0.1 * Math.sin(time * 4.0 + s);
    }

    const breathe = 0.5 + 0.5 * Math.sin(time * 1.5), breatheFast = 0.5 + 0.5 * Math.sin(time * 4.0);
    const arr = sparkGeo.attributes.position.array, disp = 0.06 * breathe + 0.04 * breatheFast + energy * 0.6;
    for (let i = 0; i < SPARK_N; i++) {
      const idx = i * 3, off = disp * Math.sin(time * 3 + sparkPhases[i]);
      arr[idx] = sparkBase[idx] + sparkNormals[idx] * off;
      arr[idx + 1] = sparkBase[idx + 1] + sparkNormals[idx + 1] * off;
      arr[idx + 2] = sparkBase[idx + 2] + sparkNormals[idx + 2] * off;
    }
    sparkGeo.attributes.position.needsUpdate = true;

    for (let c = 0; c < cellMeshes.length; c++) {
      const piece = cellMeshes[c], { phase, ringIdx, normal, mat } = piece.userData;
      const wave = Math.sin(time * 2.5 - ringIdx * 0.7 + phase);
      const waveFast = Math.sin(time * 5.0 + phase * 2.0);
      const push = Math.max(0, 0.1 * wave + 0.04 * waveFast + energy * 0.2);
      piece.position.set(normal.x * push, normal.y * push, normal.z * push);
      mat.opacity = 0.05 + 0.15 * Math.max(0, wave) + energy * 0.12;
    }
    sparkMat.opacity = 0.25 + 0.25 * breathe + energy * 0.5;
    sparkMat.size = 0.01 + 0.008 * breathe + energy * 0.006;

    const voiceActive = (st === 'speaking' || st === 'listening') ? 1 : 0;
    const voicePulseFast = 0.5 + 0.5 * Math.sin(time * 6.5);
    const sc = 1.0 + 0.03 * Math.sin(time * 1.5) + energy * (0.08 + voiceActive * 0.14) + voiceActive * 0.05 * voicePulseFast;
    globe.scale.set(sc, sc, sc);
    const glowPulse = 0.35 + 0.2 * breathe + energy * 0.25 + voiceActive * 0.25 * voicePulseFast;
    glowMat.opacity = glowPulse;
    const gs = 1.8 + 0.4 * breathe + energy * 0.5 + voiceActive * 0.6 * voicePulseFast;
    glowSprite.scale.set(gs, gs, 1);
    glassMat.uniforms.rimIntensity.value = 1.2 + voiceActive * (0.8 + energy * 1.2) * voicePulseFast;

    renderer.render(scene, camera);
  }
  animate(0);

  window.addEventListener('resize', () => {
    camera.aspect = window.innerWidth / window.innerHeight;
    camera.position.z = getCamZ();
    camera.updateProjectionMatrix();
    renderer.setSize(window.innerWidth, window.innerHeight);
  });
})();
