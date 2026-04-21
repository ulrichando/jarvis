// Particle-sphere reactor — ~3000 points arranged on a sphere surface,
// each displaced radially in response to voice activity. Inspired by
// jeromepl/3D-audio-sphere but driven by a single audioLevel signal
// (our turn.state event) instead of an FFT spectrum.
//
// Window globals exposed for the HUD:
//   setJarvisState(state, audioLevel) — called on every turn.state event
//   setJarvisAudio(level)             — shortcut for audio-only update
//
// Swap with ?reactor=particles in the HUD URL, or make default in index.html.

(function () {
  if (typeof THREE === 'undefined') return;

  // --- State shared with the HUD event handler ---
  let _state = 'idle';
  let _audioLevel = 0;
  window.setJarvisState = function (state, audioLevel) {
    _state = state;
    if (typeof audioLevel === 'number') _audioLevel = audioLevel;
  };
  window.setJarvisAudio = function (level) { _audioLevel = level || 0; };

  // Ghost silver palette — matches desktop + mobile reactor.
  const hexToInt = (h) => parseInt(h.replace('#', ''), 16);
  const SILVER = hexToInt('#e8f4ff');
  const SILVER_DIM = hexToInt('#c4d8e8');

  // --- Scene setup ---
  const scene = new THREE.Scene();
  const camera = new THREE.PerspectiveCamera(
    45, window.innerWidth / window.innerHeight, 0.1, 100,
  );
  camera.position.z = 8;

  const renderer = new THREE.WebGLRenderer({
    alpha: true, antialias: true, premultipliedAlpha: false,
  });
  renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
  renderer.setSize(window.innerWidth, window.innerHeight);
  renderer.setClearColor(0x000000, 0);
  renderer.domElement.style.cssText =
    'position:fixed;inset:0;width:100%;height:100%;background:transparent;pointer-events:none;';
  document.body.appendChild(renderer.domElement);

  // --- Particle geometry ---
  const PARTICLE_COUNT = 3000;
  const R = 1.8;

  // Per-particle static state, stored flat for speed:
  // basePos[i*3..i*3+2]  — original unit-sphere position × R
  // normal[i*3..i*3+2]   — outward direction (== basePos / R)
  // phase[i]             — random sine phase, per-point wobble
  // freq[i]              — wobble frequency multiplier
  // latBand[i]           — cos(phi) ∈ [-1,1], used to modulate intensity
  //                        by latitude so the bands pulse like the
  //                        FFT-driven version (low ≈ equator, high ≈ poles)
  const basePos = new Float32Array(PARTICLE_COUNT * 3);
  const normal = new Float32Array(PARTICLE_COUNT * 3);
  const phase = new Float32Array(PARTICLE_COUNT);
  const freq = new Float32Array(PARTICLE_COUNT);
  const latBand = new Float32Array(PARTICLE_COUNT);
  const liveOffset = new Float32Array(PARTICLE_COUNT * 3);

  for (let i = 0; i < PARTICLE_COUNT; i++) {
    // Uniform-on-sphere via inverse cosine. Avoids polar clumping that
    // random spherical coords produce.
    const u = Math.random();
    const v = Math.random();
    const theta = 2 * Math.PI * u;
    const phi = Math.acos(2 * v - 1);
    const nx = Math.sin(phi) * Math.cos(theta);
    const ny = Math.cos(phi);
    const nz = Math.sin(phi) * Math.sin(theta);
    const idx = i * 3;
    normal[idx] = nx; normal[idx + 1] = ny; normal[idx + 2] = nz;
    basePos[idx] = nx * R;
    basePos[idx + 1] = ny * R;
    basePos[idx + 2] = nz * R;
    phase[i] = Math.random() * Math.PI * 2;
    freq[i] = 0.6 + Math.random() * 2.4;
    latBand[i] = ny; // -1 south pole, +1 north pole
    liveOffset[idx] = basePos[idx];
    liveOffset[idx + 1] = basePos[idx + 1];
    liveOffset[idx + 2] = basePos[idx + 2];
  }

  const geo = new THREE.BufferGeometry();
  geo.setAttribute('position', new THREE.BufferAttribute(liveOffset, 3));

  // Circular sprite so particles don't look like little squares.
  const spriteTex = (() => {
    const size = 64;
    const c = document.createElement('canvas');
    c.width = c.height = size;
    const ctx = c.getContext('2d');
    const g = ctx.createRadialGradient(
      size / 2, size / 2, 0,
      size / 2, size / 2, size / 2,
    );
    g.addColorStop(0, 'rgba(255,255,255,1)');
    g.addColorStop(0.35, 'rgba(232,244,255,0.75)');
    g.addColorStop(0.8, 'rgba(196,216,232,0.15)');
    g.addColorStop(1, 'rgba(0,0,0,0)');
    ctx.fillStyle = g;
    ctx.fillRect(0, 0, size, size);
    const tex = new THREE.CanvasTexture(c);
    tex.premultiplyAlpha = true;
    return tex;
  })();

  const mat = new THREE.PointsMaterial({
    size: 0.05,
    map: spriteTex,
    color: SILVER,
    transparent: true,
    opacity: 0.75,
    blending: THREE.AdditiveBlending,
    depthWrite: false,
    sizeAttenuation: true,
  });

  const points = new THREE.Points(geo, mat);
  scene.add(points);

  // Inner soft glow sprite, so the middle reads as a reactor core.
  const coreMat = new THREE.SpriteMaterial({
    map: spriteTex, color: SILVER, transparent: true,
    opacity: 0.35, blending: THREE.AdditiveBlending, depthWrite: false,
  });
  const coreSprite = new THREE.Sprite(coreMat);
  coreSprite.scale.set(2.8, 2.8, 1);
  scene.add(coreSprite);

  // --- Animation ---
  let t = 0;
  let smoothAudio = 0;
  let lastFrame = -1;
  const FRAME_MS = 1000 / 60;

  const positions = geo.attributes.position.array;

  function animate(now) {
    requestAnimationFrame(animate);
    if (lastFrame >= 0 && now - lastFrame < FRAME_MS) return;
    lastFrame = now;
    t += 0.016;

    // Smooth the audio signal so particles don't jitter on single spikes.
    const raw = Math.min(1, Math.max(0, _audioLevel * 5));
    const sf = raw > smoothAudio ? 0.35 : 0.06;
    smoothAudio += (raw - smoothAudio) * sf;

    // When jarvis is speaking there's no mic level — synthesize a pulse.
    const speakPulse = _state === 'speaking'
      ? 0.35 + 0.25 * Math.sin(t * 7) * Math.sin(t * 2.3)
      : 0;
    const energy = Math.max(smoothAudio, speakPulse);

    // How far particles can push out at full energy.
    const PUSH = 0.55;
    // Baseline wobble so it's never totally still.
    const BREATHE = 0.04 + 0.04 * Math.sin(t * 0.9);

    for (let i = 0; i < PARTICLE_COUNT; i++) {
      const idx = i * 3;
      // Latitude weighting: when 'speaking', poles light up harder
      // (mimics how high-frequency energy concentrates at poles in a
      // real FFT-driven viz). When 'listening', equator pulses harder.
      const lat = latBand[i];
      let bandBoost = 1;
      if (_state === 'speaking')      bandBoost = 0.6 + Math.abs(lat) * 0.8;
      else if (_state === 'listening') bandBoost = 1.0 - Math.abs(lat) * 0.4;

      const wobble = Math.sin(t * freq[i] + phase[i]);
      const disp = BREATHE + energy * PUSH * bandBoost * (0.6 + 0.4 * wobble);

      positions[idx]     = basePos[idx]     * (1 + disp) + normal[idx]     * 0.05 * wobble;
      positions[idx + 1] = basePos[idx + 1] * (1 + disp) + normal[idx + 1] * 0.05 * wobble;
      positions[idx + 2] = basePos[idx + 2] * (1 + disp) + normal[idx + 2] * 0.05 * wobble;
    }
    geo.attributes.position.needsUpdate = true;

    // Gentle rotation so it feels alive + unmistakably 3D.
    points.rotation.y += 0.0025;
    points.rotation.x = Math.sin(t * 0.3) * 0.12;

    // Core + size pulse with energy.
    mat.size = 0.04 + energy * 0.07;
    mat.opacity = 0.55 + energy * 0.4;
    coreMat.opacity = 0.25 + energy * 0.6;
    const cs = 2.6 + energy * 1.8;
    coreSprite.scale.set(cs, cs, 1);

    // Faint color shift — still ghost silver, but lighter at higher energy.
    mat.color.setHex(energy > 0.3 ? SILVER : SILVER_DIM);

    renderer.render(scene, camera);
  }
  animate(0);

  window.addEventListener('resize', () => {
    camera.aspect = window.innerWidth / window.innerHeight;
    camera.updateProjectionMatrix();
    renderer.setSize(window.innerWidth, window.innerHeight);
  });
})();
