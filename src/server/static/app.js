// J.A.R.V.I.S. MARK V — OS Boot Mode: Always On, Always Listening

const WS_URL = `ws://${window.location.host}/ws`;

// Detect desktop (Tauri) vs web mode — check __TAURI__ or ?desktop=1 param
const IS_DESKTOP = !!(window.__TAURI__) || new URLSearchParams(window.location.search).has('desktop');
const MODE_CLASS = IS_DESKTOP ? 'desktop-mode' : 'web-mode';
document.documentElement.classList.add(MODE_CLASS);
document.body.classList.add(MODE_CLASS);

let ws = null;
let isSpeaking = false;
let speechEndTime = 0;  // timestamp when TTS last stopped (echo suppression)
let ttsOnly = true;  // When true, Jarvis speaks but doesn't show text on screen

// Audio
let audioContext = null;
let micAnalyser = null;
let micDataArray = null;
let micLevel = 0, ttsLevel = 0, smoothLevel = 0;
let micStream = null;

// Recording (MediaRecorder-based STT)
let mediaRecorder = null;
let audioChunks = [];
let isRecording = false;
let silenceTimer = null;
let recordingStartTime = 0;

// Server-side mic — when active, server handles TTS playback via system audio
let serverMicActive = false;

// HUD
let panelIndex = 0;
const PANELS = ['hud-right', 'hud-left', 'hud-bottom', 'hud-top'];
const MAX_CARDS = 4;
let currentMode = 'normal';

// Vision debug (off — removed from UI)
let visionDebugActive = false;

// Notify server of TTS state so ambient listener can suppress echo
function notifyTTSState(speaking) {
    if (ws && ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({ type: 'tts_state', speaking }));
    }
}

// Ensure audio is unlocked (Tauri/webview fix — AudioContext must be resumed)
async function ensureAudioUnlocked() {
    if (audioContext && audioContext.state === 'suspended') {
        try { await audioContext.resume(); } catch (e) {}
    }
}

// ── DOM ───────────────────────────────────────────────────────────

const reactor = document.getElementById('reactor');
const waveCanvas = document.getElementById('waveform-canvas');
const waveCtx = waveCanvas.getContext('2d');
const coreGlow = document.querySelector('.core-glow');
const mediaOverlay = document.getElementById('media-overlay');
const mediaContent = document.getElementById('media-content');
const mediaClose = document.getElementById('media-close');

// Fallback text input (Tab)
const fallbackInput = document.createElement('input');
fallbackInput.type = 'text';
fallbackInput.placeholder = 'Type your message...';
fallbackInput.style.cssText = 'position:fixed;bottom:30px;left:50%;transform:translateX(-50%);width:350px;background:rgba(0,20,40,0.9);border:1px solid rgba(0,212,255,0.2);color:#b0c8d4;padding:8px 14px;font-family:monospace;font-size:0.8rem;outline:none;display:none;z-index:99;';
document.body.appendChild(fallbackInput);

let fallbackVisible = false;
document.addEventListener('keydown', (e) => {
    if (e.key === 'Tab') { e.preventDefault(); fallbackVisible = !fallbackVisible; fallbackInput.style.display = fallbackVisible ? 'block' : 'none'; if (fallbackVisible) fallbackInput.focus(); }
    if (e.key === 'Escape') { closeMedia(); stopSpeaking(); if (document.fullscreenElement) document.exitFullscreen(); }
    if (e.key === 'f' && !fallbackVisible) { document.documentElement.requestFullscreen().catch(()=>{}); }
});
fallbackInput.addEventListener('keydown', (e) => { if (e.key === 'Enter') { const t = fallbackInput.value.trim(); if (t) { fallbackInput.value = ''; sendQuery(t); } } });

// Click anywhere while JARVIS is speaking = stop
document.addEventListener('click', (e) => {
    if (e.target.closest('#media-overlay') || e.target.closest('.hud-card') ||
        e.target === fallbackInput) return;
    if (isSpeaking) {
        stopSpeaking();
    }
});

// ── HUD CARDS ─────────────────────────────────────────────────────

function addCard(type, content, panel) {
    const target = panel || PANELS[panelIndex++ % PANELS.length];
    const container = document.getElementById(target);
    const card = document.createElement('div');
    card.className = `hud-card ${type}`;
    if (type === 'response') card.innerHTML = escapeHtml(content);
    else if (type === 'suggestion') { card.innerHTML = escapeHtml(content); card.onclick = () => { sendQuery('More on: ' + content); removeCard(card); }; }
    else if (type === 'info') card.innerHTML = content;
    container.appendChild(card);
    while (container.children.length > MAX_CARDS) { const old = container.firstChild; old.classList.add('fade-out'); setTimeout(() => old.remove(), 500); }
    setTimeout(() => removeCard(card), type === 'suggestion' ? 15000 : 25000);
    return card;
}
function removeCard(card) { if (card.parentNode) { card.classList.add('fade-out'); setTimeout(() => card.remove(), 500); } }

// ── MEDIA ─────────────────────────────────────────────────────────

function showMedia(type, content) {
    mediaOverlay.classList.remove('hidden');
    if (type === 'image') {
        mediaContent.innerHTML = `<img src="${escapeAttr(content)}" alt="">`;
    } else if (type === 'video') {
        mediaContent.innerHTML = `<video src="${escapeAttr(content)}" controls autoplay></video>`;
    } else if (type === 'text') {
        mediaContent.innerHTML = `<div class="media-text">${escapeHtml(content)}</div>`;
    }
}
function closeMedia() { mediaOverlay.classList.add('hidden'); mediaContent.innerHTML = ''; }
mediaClose.addEventListener('click', closeMedia);

// ── AUDIO CAPTURE ─────────────────────────────────────────────────

async function initAudio() {
    // Request mic access — this triggers the browser permission indicator
    if (!audioContext) {
        audioContext = new (window.AudioContext || window.webkitAudioContext)();
    }
    if (audioContext.state === 'suspended') await audioContext.resume();

    // Single request for mic + camera (one permission prompt)
    const stream = await navigator.mediaDevices.getUserMedia({
        audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true, autoGainControl: true },
        video: { width: 320, height: 240, frameRate: 2 }
    }).catch(async () => {
        // Fallback: audio only if camera denied
        return await navigator.mediaDevices.getUserMedia({
            audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true, autoGainControl: true }
        });
    });
    micStream = stream;
    console.log('[JARVIS] Mic access granted — stream active');

    // Give webcam a video-only stream (no audio leak to speakers)
    const videoTrack = stream.getVideoTracks()[0];
    if (videoTrack) {
        const videoOnly = new MediaStream([videoTrack]);
        const webcam = document.getElementById('webcam');
        webcam.srcObject = videoOnly;
        startAmbientVision(webcam);
        console.log('[JARVIS] Camera access granted — vision active');
    }

    const source = audioContext.createMediaStreamSource(stream);
    micAnalyser = audioContext.createAnalyser();
    micAnalyser.fftSize = 256;
    micAnalyser.smoothingTimeConstant = 0.7;
    source.connect(micAnalyser);
    micDataArray = new Uint8Array(micAnalyser.frequencyBinCount);
    readMicLevel();

    // JARVIS is always listening — start ambient immediately
    startAmbientListening();
}

function readMicLevel() {
    if (!micAnalyser) return;
    micAnalyser.getByteFrequencyData(micDataArray);
    let sum = 0;
    for (let i = 0; i < micDataArray.length; i++) sum += micDataArray[i] * micDataArray[i];
    micLevel = Math.min(1, (Math.sqrt(sum / micDataArray.length) / 255) * 3);
    // NO interrupt here — mic picks up speaker output and causes false triggers
    requestAnimationFrame(readMicLevel);
}

// ── WAVEFORM ──────────────────────────────────────────────────────

const DOTS = 120;
let dotLevels = new Float32Array(DOTS);
let targetLevels = new Float32Array(DOTS);
const dotOffsets = new Float32Array(DOTS);
for (let i = 0; i < DOTS; i++) dotOffsets[i] = Math.random() * Math.PI * 2;

function drawWaveform() {
    const w = waveCanvas.width, h = waveCanvas.height;
    const cx = w / 2, cy = h / 2, radius = w * 0.42;
    waveCtx.clearRect(0, 0, w, h);
    for (let i = 0; i < DOTS; i++) {
        dotLevels[i] += (targetLevels[i] - dotLevels[i]) * 0.15;
        const angle = (i / DOTS) * Math.PI * 2 - Math.PI / 2;
        const level = dotLevels[i], size = 1.5 + level * 5;
        const x = cx + Math.cos(angle) * radius, y = cy + Math.sin(angle) * radius;
        const alpha = 0.12 + level * 0.75;
        waveCtx.beginPath(); waveCtx.arc(x, y, size + 2, 0, Math.PI * 2);
        waveCtx.fillStyle = `rgba(0,170,255,${alpha * 0.25})`; waveCtx.fill();
        waveCtx.beginPath(); waveCtx.arc(x, y, size, 0, Math.PI * 2);
        waveCtx.fillStyle = `rgba(0,170,255,${alpha})`; waveCtx.fill();
        if (level > 0.4) { waveCtx.beginPath(); waveCtx.arc(x, y, size * 0.4, 0, Math.PI * 2); waveCtx.fillStyle = `rgba(150,220,255,${(level - 0.4) * 1.5})`; waveCtx.fill(); }
    }
    requestAnimationFrame(drawWaveform);
}

function updateWaveform() {
    const active = isSpeaking ? ttsLevel : micLevel;
    smoothLevel += (active - smoothLevel) * 0.2;
    const t = Date.now() / 1000;
    if (smoothLevel > 0.05) {
        for (let i = 0; i < DOTS; i++) {
            let f = smoothLevel;
            if (micDataArray && !isSpeaking) f = (micDataArray[Math.floor((i / DOTS) * micDataArray.length)] / 255) * 2;
            const w = Math.sin(t * 3 + dotOffsets[i]) * smoothLevel * 0.3;
            const p = (i - 1 + DOTS) % DOTS, n = (i + 1) % DOTS;
            targetLevels[i] = Math.min(1, Math.max(0.03, (f + w) * 0.7 + (targetLevels[p] + targetLevels[n]) * 0.15));
        }
    } else {
        for (let i = 0; i < DOTS; i++) targetLevels[i] = Math.max(0.03, Math.sin(t * 0.5 + i * 0.12) * 0.12 + 0.15 + Math.sin(t * 0.8 + i * 0.07 + dotOffsets[i]) * 0.08);
    }
    if (coreGlow) { const gi = 0.3 + smoothLevel * 0.7, gr = 40 + smoothLevel * 40; coreGlow.style.boxShadow = `0 0 ${gr}px rgba(0,184,212,${gi}), 0 0 ${gr * 2}px rgba(0,184,212,${gi * 0.3})`; }
}
setInterval(updateWaveform, 33);
drawWaveform();

let ttsInterval = null;
function startTTSPulse() { let p = 0; ttsInterval = setInterval(() => { p += 0.4; ttsLevel = Math.abs(Math.sin(p)) * 0.7 * (Math.sin(p * 0.3) * 0.2 + 0.3) + 0.15; }, 50); }
function stopTTSPulse() { clearInterval(ttsInterval); ttsLevel = 0; }

// ── THINKING SOUND — ambient tone while processing ────────────────

let _thinkingAudio = null;

function _startThinkingSound() {
    _stopThinkingSound();
    _thinkingAudio = new Audio('/static/thinking.mp3');
    _thinkingAudio.loop = true;
    _thinkingAudio.volume = 0.3;
    _thinkingAudio.play().catch(() => {});
}

function _stopThinkingSound() {
    if (_thinkingAudio) {
        _thinkingAudio.pause();
        _thinkingAudio.currentTime = 0;
        _thinkingAudio = null;
    }
}

// ── SERVER AUDIO PLAYER — real-time volume drives reactor ─────────

let _ttsAnalyser = null;
let _ttsDataArray = null;
let _ttsAnimFrame = null;

function _playServerAudio(arrayBuffer) {
    // Stop any current speech
    stopSpeaking();

    // Ensure AudioContext exists
    if (!audioContext) {
        audioContext = new (window.AudioContext || window.webkitAudioContext)();
    }
    if (audioContext.state === 'suspended') audioContext.resume();

    // Decode and play via AudioBuffer — works reliably in all webviews
    audioContext.decodeAudioData(arrayBuffer.slice(0), (audioBuffer) => {
        // Create source
        const source = audioContext.createBufferSource();
        source.buffer = audioBuffer;

        // Analyser for real-time volume
        _ttsAnalyser = audioContext.createAnalyser();
        _ttsAnalyser.fftSize = 256;
        _ttsAnalyser.smoothingTimeConstant = 0.7;
        _ttsDataArray = new Uint8Array(_ttsAnalyser.frequencyBinCount);

        source.connect(_ttsAnalyser);
        _ttsAnalyser.connect(audioContext.destination);

        // Track the source so stopSpeaking can stop it
        currentAudio = source;

        isSpeaking = true;
        setReactorState('speaking');
        _startRealTimePulse();

        source.onended = () => {
            _stopRealTimePulse();
            isSpeaking = false;
            setReactorState('');
            currentAudio = null;
        };

        source.start(0);
    }, (err) => {
        // Decode failed — ffplay still plays the audio through system speakers.
        // Use fake pulse for reactor animation as fallback.
        console.warn('[JARVIS] Audio decode failed, using fallback pulse:', err);
        isSpeaking = true;
        setReactorState('speaking');
        startTTSPulse();
    });
}

function _startRealTimePulse() {
    // Read analyser and feed ttsLevel from real audio volume
    function _tick() {
        if (!_ttsAnalyser || !isSpeaking) return;
        _ttsAnalyser.getByteFrequencyData(_ttsDataArray);
        let sum = 0;
        for (let i = 0; i < _ttsDataArray.length; i++) sum += _ttsDataArray[i] * _ttsDataArray[i];
        ttsLevel = Math.min(1, (Math.sqrt(sum / _ttsDataArray.length) / 255) * 3);
        _ttsAnimFrame = requestAnimationFrame(_tick);
    }
    _tick();
}

function _stopRealTimePulse() {
    if (_ttsAnimFrame) cancelAnimationFrame(_ttsAnimFrame);
    _ttsAnimFrame = null;
    _ttsAnalyser = null;
    _ttsDataArray = null;
    ttsLevel = 0;
}

// ── SVG TICKS ─────────────────────────────────────────────────────

(function() {
    const o = document.getElementById('ticks-outer'), m = document.getElementById('ticks-mid');
    for (let i = 0; i < 60; i++) {
        const a = (i / 60) * 360, major = i % 5 === 0;
        const r = document.createElementNS('http://www.w3.org/2000/svg', 'rect');
        r.setAttribute('x', '249'); r.setAttribute('y', '6');
        r.setAttribute('width', major ? '2.5' : '1'); r.setAttribute('height', major ? '10' : '6');
        r.setAttribute('transform', `rotate(${a} 250 250)`);
        r.setAttribute('class', major ? 'tick-mark' : 'tick-mark minor');
        r.setAttribute('rx', '0.5'); o.appendChild(r);
    }
    for (let i = 0; i < 36; i++) {
        const a = (i / 36) * 360;
        const r = document.createElementNS('http://www.w3.org/2000/svg', 'rect');
        r.setAttribute('x', '209.5'); r.setAttribute('y', '5'); r.setAttribute('width', '1'); r.setAttribute('height', '7');
        r.setAttribute('transform', `rotate(${a} 210 210)`);
        r.setAttribute('class', 'tick-mark minor'); r.setAttribute('rx', '0.5'); m.appendChild(r);
    }
})();

// ── WEBSOCKET ─────────────────────────────────────────────────────

let wsReady = null;  // Promise that resolves when WS is connected

function connect() {
    return new Promise((resolve) => {
        try { ws = new WebSocket(WS_URL); } catch (e) { resolve(); return; }
        ws.binaryType = 'arraybuffer';

        // Timeout — don't hang forever if server is down
        const timeout = setTimeout(() => { resolve(); }, 5000);

        ws.onopen = () => {
            clearTimeout(timeout);
            console.log('[JARVIS] WebSocket connected');
            resolve();
            // If ambient was waiting for WS, start it now
            if (audioInitialized && !ambientActive && micStream) {
                startAmbientListening();
            }
        };
        ws.onerror = () => { clearTimeout(timeout); resolve(); };
        ws.onmessage = (e) => {
            if (e.data instanceof ArrayBuffer) {
                // Server sends binary audio for reactor sync only.
                // Audio playback is handled by ffplay on the server side.
                // Just ignore binary data — reactor syncs via status events.
                return;
            }
            try {
                const data = JSON.parse(e.data);
                if (data.type === 'stt_result') {
                    updateMicUI(false);
                    serverMicActive = true;
                    // Server-side mic already handles the query — just show what was heard
                    addCard('info', '🎤 ' + escapeHtml(data.text));
                } else if (data.type === 'stt_status') {
                    if (data.status === 'transcribing') {
                        setReactorState('thinking');
                        updateMicUI(false, 'Transcribing...');
                    } else if (data.status === 'no_speech') {
                        setReactorState('');
                        updateMicUI(false, 'No speech detected');
                        setTimeout(() => updateMicUI(false), 2000);
                    }
                } else if (data.type === 'stt_error') {
                    console.error('[JARVIS] STT error:', data.error);
                    setReactorState('');
                    updateMicUI(false, 'Error');
                    setTimeout(() => updateMicUI(false), 2000);
                } else {
                    handleMessage(data);
                }
            } catch(err) {}
        };
        ws.onclose = () => {
            clearTimeout(timeout);
            console.log('[JARVIS] WebSocket disconnected — reconnecting in 3s...');
            ambientActive = false;  // Mark ambient as inactive until reconnect
            setTimeout(() => { wsReady = connect(); }, 3000);
        };
    });
}
function send(data) { if (ws && ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify(data)); }
function sendQuery(text) { setReactorState('activated'); send({ type: 'query', text }); }

// ── MESSAGE HANDLING ──────────────────────────────────────────────

function handleMessage(data) {
    if (data.type === 'message' && data.role === 'jarvis') {
        setReactorState('');
        const content = data.content;
        if (!content || content.trim() === '') return;
        // Pause mic when CLI opens — cancel any active recording
        if (content === '__PAUSE_MIC__') {
            cancelRecording();
            return;
        }
        // Settings panel — triggered by "settings" / "providers" voice command
        if (content === '__SETTINGS__') {
            showSettings();
            return;
        }
        // TTS-only toggle commands
        if (content === '__SHOW_TEXT__') {
            ttsOnly = false;
            addCard('info', 'Text display: ON');
            return;
        }
        if (content === '__HIDE_TEXT__') {
            ttsOnly = true;
            addCard('info', 'Text display: OFF — voice only');
            setTimeout(() => {
                document.querySelectorAll('.hud-card').forEach(c => removeCard(c));
            }, 2000);
            return;
        }
        // Window control commands (for Tauri desktop app)
        if (content === '__MINIMIZE__') {
            if (window.__TAURI__) {
                window.__TAURI__.window.appWindow.minimize();
            } else {
                // Browser fallback — can't truly minimize, but hide the UI
                document.getElementById('app').style.opacity = '0.05';
            }
            return;
        }
        if (content === '__MAXIMIZE__') {
            if (window.__TAURI__) {
                window.__TAURI__.window.appWindow.toggleMaximize();
            } else {
                document.documentElement.requestFullscreen().catch(() => {});
            }
            return;
        }
        if (content === '__RESTORE__') {
            if (window.__TAURI__) {
                window.__TAURI__.window.appWindow.unminimize();
                window.__TAURI__.window.appWindow.show();
                window.__TAURI__.window.appWindow.setFocus();
            } else {
                document.getElementById('app').style.opacity = '1';
                if (document.fullscreenElement) document.exitFullscreen();
            }
            return;
        }
        if (content.includes('Berbon mode active')) currentMode = 'berbon';
        else if (content.includes('CLI mode')) currentMode = 'cli';
        else if (content.includes('Back to normal')) currentMode = 'normal';
        const voiceStyle = data.voice_style || 'default';
        // Use server-cleaned 'spoken' field — no code, no paths, no scripts
        const spoken = data.spoken || '';
        if (checkMediaCommand(content)) return;

        // Partial message = first sentence from streaming response — speak immediately
        if (data.partial) {
            if (!serverMicActive && spoken.length > 0) {
                speakResponse(spoken, voiceStyle);
            }
            return;  // Wait for final message with full content
        }

        // Final message after a partial — update display, queue remaining speech
        if (data.final && !ttsOnly) {
            const model = data.model || '';
            const modelTag = model ? `<div style="font-size:0.55rem;color:var(--text-dim);margin-top:4px;">${escapeHtml(model)} · ${data.latency_ms || 0}ms</div>` : '';
            addCard('info', escapeHtml(content) + modelTag);
            // Don't re-speak — partial already started TTS
            return;
        }

        // Show text only if ttsOnly is off
        if (!ttsOnly) {
            const model = data.model || '';
            const modelTag = model ? `<div style="font-size:0.55rem;color:var(--text-dim);margin-top:4px;">${escapeHtml(model)} · ${data.latency_ms || 0}ms</div>` : '';
            addCard('info', escapeHtml(content) + modelTag);
        }
        // Only use browser TTS if server isn't handling audio via ffplay.
        if (!serverMicActive) {
            if (currentMode === 'cli' || currentMode === 'berbon') {
                if (spoken.length > 0 && spoken.length < 100) speakResponse(spoken, voiceStyle);
            } else {
                if (spoken.length > 0) speakResponse(spoken, voiceStyle);
            }
        }
    } else if (data.type === 'status' && data.status === 'thinking') {
        setReactorState('thinking');
        _startThinkingSound();
    } else if (data.type === 'status' && data.status === 'speaking') {
        _stopThinkingSound();
        setReactorState('speaking');
        isSpeaking = true;
        startTTSPulse();
    } else if (data.type === 'status' && data.status === '') {
        _stopThinkingSound();
        setReactorState('');
        isSpeaking = false;
        stopTTSPulse();
    } else if (data.type === 'mic_level') {
        // Server-side mic level — drive reactor when user speaks
        micLevel = data.level;
        if (data.level > 0.1 && !isSpeaking) {
            setReactorState('hearing');
        } else if (data.level < 0.05 && !isSpeaking) {
            setReactorState('');
        }
    } else if (data.type === 'suggestion') {
        addCard('suggestion', data.content);
    } else if (data.type === 'vision_event') {
        const ev = data.event;
        if (ev.type === 'person_appeared') {
            console.log('[JARVIS] Person detected:', ev);
        } else if (ev.type === 'person_left') {
            console.log('[JARVIS] Person left view');
        }
    } else if (data.type === 'face_id_status') {
        console.log('[FACE-ID]', data.message || data.status);
    } else if (data.type === 'face_id_result') {
        console.log('[FACE-ID] Verify:', data.verified ? 'MATCH' : 'NO MATCH', data.message);
    } else if (data.type === 'face_id_list') {
        console.log('[FACE-ID] Enrolled:', data.enrolled);
    } else if (data.type === 'providers') {
        renderProviders(data.providers);
    } else if (data.type === 'provider_result') {
        if (data.providers) renderProviders(data.providers);
        if (data.success) {
            addCard('info', `<span style="color:var(--teal)">✓</span> ${escapeHtml(data.message)}`);
        } else {
            addCard('info', `<span style="color:var(--red)">✗</span> ${escapeHtml(data.error)}`);
        }
    }
}

function checkMediaCommand(text) {
    const lower = text.toLowerCase();
    const imgMatch = text.match(/\[show:image\](.*?)\[\/show\]/i);
    if (imgMatch) { showMedia('image', imgMatch[1].trim()); const c = text.replace(/\[show:image\].*?\[\/show\]/gi, '').trim(); if (c) speakResponse(c); return true; }
    const vidMatch = text.match(/\[show:video\](.*?)\[\/show\]/i);
    if (vidMatch) { showMedia('video', vidMatch[1].trim()); const c = text.replace(/\[show:video\].*?\[\/show\]/gi, '').trim(); if (c) speakResponse(c); return true; }
    const txtMatch = text.match(/\[show:text\](.*?)\[\/show\]/is);
    if (txtMatch) { showMedia('text', txtMatch[1].trim()); const c = text.replace(/\[show:text\].*?\[\/show\]/gis, '').trim(); if (c) speakResponse(c); return true; }
    return false;
}

function setReactorState(state) { reactor.className = state; }

// ── VOICE — MediaRecorder + Server-Side Whisper STT ─────────────

// ── AMBIENT LISTENING — always on, no button needed ───────────────
//
// Streams raw PCM audio to the server continuously.
// Server detects speech boundaries and transcribes with Whisper.
// No mic button, no wake word, no browser Speech API.
// Works in ANY browser/webview — all intelligence is server-side.

let ambientProcessor = null;
let ambientActive = false;

function startAmbientListening() {
    if (ambientActive || !audioContext || !micStream) return;
    if (!ws || ws.readyState !== WebSocket.OPEN) {
        console.log('[JARVIS] WS not ready — ambient will start when connected');
        return;
    }

    try {
        const source = audioContext.createMediaStreamSource(micStream);
        const sampleRate = audioContext.sampleRate;
        const bufferSize = 4096;
        let chunkCount = 0;

        // Use ScriptProcessorNode (widely supported)
        ambientProcessor = audioContext.createScriptProcessor(bufferSize, 1, 1);

        ambientProcessor.onaudioprocess = (event) => {
            // Suppress mic while JARVIS speaks + 0.8s after (prevents echo)
            if (isSpeaking || (speechEndTime && Date.now() - speechEndTime < 800)) return;
            if (!ws || ws.readyState !== WebSocket.OPEN) return;

            const input = event.inputBuffer.getChannelData(0);

            // Downsample to 16kHz
            const ratio = sampleRate / 16000;
            const outLen = Math.floor(input.length / ratio);
            const output = new Float32Array(outLen);
            for (let i = 0; i < outLen; i++) {
                output[i] = input[Math.floor(i * ratio)];
            }

            // Send as binary
            try {
                ws.send(output.buffer);
                chunkCount++;
                if (chunkCount === 1) console.log('[JARVIS] First audio chunk sent to server');
                if (chunkCount % 200 === 0) console.log('[JARVIS] Audio chunks sent:', chunkCount);
            } catch (e) {}
        };

        source.connect(ambientProcessor);
        // Route to a silent dummy destination (never hits speakers)
        const silentDest = audioContext.createMediaStreamDestination();
        ambientProcessor.connect(silentDest);

        ambientActive = true;
        updateMicUI(true, '');
        console.log('[JARVIS] Ambient listening active. Sample rate:', sampleRate, '→ 16kHz. Buffer:', bufferSize);
    } catch (e) {
        console.error('[JARVIS] Ambient listening failed:', e);
    }
}

function stopAmbientListening() {
    if (ambientProcessor) {
        try { ambientProcessor.disconnect(); } catch (e) {}
        ambientProcessor = null;
    }
    ambientActive = false;
    updateMicUI(false, '');
}

// Legacy compatibility
function startRecording() { startAmbientListening(); }
function stopRecording() {}  // Ambient doesn't stop
function cancelRecording() { stopAmbientListening(); }

function updateMicUI(listening, statusText) {
    // No separate UI — reactor core shows state
    // The waveform + core glow already react to micLevel
}

// Show "hearing" state on reactor when mic picks up speech
setInterval(() => {
    if (ambientActive && micLevel > 0.05 && !isSpeaking) {
        if (!reactor.classList.contains('hearing')) {
            reactor.classList.add('hearing');
        }
    } else {
        reactor.classList.remove('hearing');
    }
}, 100);

// ── TTS — Chunked speech with natural pauses ─────────────────────
//
// JARVIS speaks in chunks — like a human taking breaths between thoughts.
// Each chunk is a sentence or phrase. Between chunks, silence.
// Prefetches next chunk while playing current one for zero-gap transitions.

let currentAudio = null;
let speechQueue = [];        // Queue of chunks waiting to be spoken
let speakingChunk = false;   // Currently playing a chunk
let speechAborted = false;   // User interrupted
let currentVoiceStyle = 'default';
let _prefetchedAudio = null; // Pre-loaded next chunk for instant playback
let _prefetchedChunk = null; // Which chunk was prefetched

function _cleanForSpeech(text) {
    return text
        .replace(/\[show:\w+\]/gi, '')
        .replace(/\[\/show\]/gi, '')
        .replace(/\[run:.*?\]/gi, '')
        .replace(/\[display:\w+\]/gi, '')
        .replace(/```[\s\S]*?```/g, '')
        .replace(/`[^`]+`/g, '')
        .replace(/https?:\/\/\S+/g, '')
        .replace(/\/[\w\/\.\-]+/g, ' ')
        .replace(/\s*-{2,}\w[\w-]*/g, '')
        .replace(/^[\s]*[\$#>].*$/gm, '')
        .replace(/drwx.*$/gm, '')
        .replace(/-rw[r-].*$/gm, '')
        .replace(/total \d+/g, '')
        .replace(/\[Plugin\].*$/gm, '')
        .replace(/Traceback.*$/gm, '')
        .replace(/File ".*$/gm, '')
        .replace(/^\s*\d+\.\d+\.\d+\.\d+.*$/gm, '')
        .replace(/[{}()\[\];=<>|\\]/g, '')
        .replace(/^\s*(import|from|def |class |if |for |while |return |print)\b.*$/gm, '')
        .replace(/\w+\.\w+\.\w+/g, '')
        .replace(/\n+/g, ' ')
        .replace(/\s{2,}/g, ' ')
        .trim();
}

function speakResponse(text, voiceStyle) {
    speechAborted = false;

    let cleanText = _cleanForSpeech(text);
    if (!cleanText || cleanText.length < 2) return;

    stopSpeaking();
    currentVoiceStyle = voiceStyle || 'default';
    speechAborted = false;

    // Immediately suppress mic
    isSpeaking = true;
    notifyTTSState(true);

    // Request chunked speech plan from server
    fetch(`/tts/chunks?text=${encodeURIComponent(cleanText)}&style=${currentVoiceStyle}`)
        .then(r => r.json())
        .then(data => {
            if (speechAborted) return;
            const chunks = data.chunks || [];
            if (chunks.length === 0) return;

            if (chunks.length === 1) {
                _speakSingle(cleanText);
                return;
            }

            speechQueue = chunks.map(c => ({
                text: c.text,
                pauseAfter: c.pause_after_ms || 0,
                important: c.is_important || false,
            }));
            _playNextChunk();
        })
        .catch(() => {
            _speakSingle(cleanText);
        });
}

function _prefetchNext() {
    // Prefetch the next chunk's audio while current one plays
    if (speechQueue.length === 0 || speechAborted) return;

    const next = speechQueue[0];
    if (_prefetchedChunk === next) return; // Already prefetching this one

    _prefetchedChunk = next;
    const url = `/tts?text=${encodeURIComponent(next.text)}&style=${currentVoiceStyle}`;
    const audio = new Audio();
    audio.preload = 'auto';
    audio.src = url;
    _prefetchedAudio = audio;
}

function _playNextChunk() {
    if (speechAborted || speechQueue.length === 0) {
        _finishSpeaking();
        return;
    }

    const chunk = speechQueue.shift();
    speakingChunk = true;

    // Use prefetched audio if available, otherwise create new
    let audio;
    if (_prefetchedAudio && _prefetchedChunk === chunk) {
        audio = _prefetchedAudio;
        _prefetchedAudio = null;
        _prefetchedChunk = null;
    } else {
        const url = `/tts?text=${encodeURIComponent(chunk.text)}&style=${currentVoiceStyle}`;
        audio = new Audio(url);
    }
    currentAudio = audio;

    currentAudio.onplay = () => {
        isSpeaking = true;
        notifyTTSState(true);
        setReactorState('speaking');
        startTTSPulse();
        // Start prefetching next chunk immediately when playback begins
        _prefetchNext();
    };

    currentAudio.onended = () => {
        speakingChunk = false;
        currentAudio = null;

        if (speechAborted) {
            _finishSpeaking();
            return;
        }

        // Natural pause between chunks — JARVIS "breathes"
        if (chunk.pauseAfter > 0 && speechQueue.length > 0) {
            stopTTSPulse();
            setTimeout(() => {
                if (!speechAborted) {
                    _playNextChunk();
                } else {
                    _finishSpeaking();
                }
            }, chunk.pauseAfter);
        } else {
            _playNextChunk();
        }
    };

    currentAudio.onerror = () => {
        speakingChunk = false;
        currentAudio = null;
        if (!speechAborted && speechQueue.length > 0) {
            _playNextChunk();
        } else {
            _finishSpeaking();
        }
    };

    ensureAudioUnlocked().then(() => {
        currentAudio.play().catch((e) => {
            console.warn('[JARVIS] TTS play failed:', e.message);
            speakingChunk = false;
            _finishSpeaking();
        });
    });
}

function _speakSingle(text) {
    // Single-shot speech — used for short responses or fallback
    const url = `/tts?text=${encodeURIComponent(text)}&style=${currentVoiceStyle}`;
    currentAudio = new Audio(url);

    currentAudio.onplay = () => {
        isSpeaking = true;
        notifyTTSState(true);
        setReactorState('speaking');
        startTTSPulse();
    };

    currentAudio.onended = () => { _finishSpeaking(); };
    currentAudio.onerror = () => { _finishSpeaking(); };
    ensureAudioUnlocked().then(() => {
        currentAudio.play().catch((e) => {
            console.warn('[JARVIS] TTS play failed:', e.message);
            _finishSpeaking();
        });
    });
}

function _finishSpeaking() {
    isSpeaking = false;
    speechEndTime = Date.now();
    notifyTTSState(false);
    speakingChunk = false;
    stopTTSPulse();
    setReactorState('');
    currentAudio = null;
    speechQueue = [];
}

function stopSpeaking() {
    speechAborted = true;
    speechQueue = [];
    _prefetchedAudio = null;
    _prefetchedChunk = null;
    _stopRealTimePulse();
    if (currentAudio) {
        try { currentAudio.stop(); } catch(e) {}
        try { currentAudio.pause(); currentAudio.currentTime = 0; } catch(e) {}
        currentAudio = null;
    }
    if (window.speechSynthesis) speechSynthesis.cancel();
    isSpeaking = false;
    speechEndTime = Date.now();
    notifyTTSState(false);
    speakingChunk = false;
    stopTTSPulse();
    setReactorState('');
}

// ── UTILS ─────────────────────────────────────────────────────────

function escapeHtml(t) { const d = document.createElement('div'); d.textContent = t; return d.innerHTML; }
function escapeAttr(t) { return t.replace(/"/g, '&quot;').replace(/'/g, '&#39;'); }

// ── BOOT SEQUENCE — JARVIS powers on like an OS ─────────────────
//
// No buttons. No prompts. No waiting.
// Power on → systems init → fully operational.
// Stays alive until explicitly shut down.

let audioInitialized = false;
let bootComplete = false;

const BOOT_STAGES = [
    { system: 'CORE',        msg: 'Neural core initializing...' },
    { system: 'NETWORK',     msg: 'WebSocket uplink established' },
    { system: 'AUDIO',       msg: 'Audio subsystem online' },
    { system: 'PERCEPTION',  msg: 'Ambient listening active' },
    { system: 'READY',       msg: 'All systems operational' },
];

function bootLog(stage, status) {
    const info = BOOT_STAGES[stage] || { system: '???', msg: status };
    const label = `[${info.system}]`;
    console.log(`[JARVIS BOOT] ${label} ${status || info.msg}`);
    // Silent boot — no HUD clutter. Check console for diagnostics.
}

async function boot() {
    // Stage 0: Core init
    bootLog(0);

    // Stage 1: Network — connect WebSocket and WAIT for it
    wsReady = connect();
    await wsReady;
    bootLog(1);

    // Stage 2: Audio — request mic, start ambient
    bootLog(2, 'Requesting audio access...');
    await bootAudio();

    // Stage 3: Perception
    if (ambientActive) {
        bootLog(3);
    } else {
        bootLog(3, 'Ambient listening standby — awaiting mic grant');
    }

    // Stage 4: Fully operational
    bootComplete = true;
    bootLog(4);

    // Notify server that JARVIS has booted
    send({ type: 'boot', status: 'complete', ambient: ambientActive });
}

async function bootAudio() {
    // Try to init mic — getUserMedia will trigger browser permission prompt
    // If permission is already granted, it succeeds instantly (no prompt)
    // If it needs a gesture, we catch and fall back to interaction handler

    // Attempt 1: Direct request (works if permission granted OR browser allows on load)
    try {
        await initAudio();
        audioInitialized = true;
        bootLog(2, 'Audio subsystem online');
        return;
    } catch (e) {
        console.log('[JARVIS] Direct mic request failed:', e.name, '— waiting for interaction');
    }

    // Attempt 2: Wait for ANY user interaction, then request mic
    // This catches the case where browser requires a gesture
    audioInitialized = false;
    bootLog(2, 'Mic needs permission — touch/click/press any key');

    await new Promise((resolve) => {
        const events = ['click', 'keydown', 'touchstart', 'mousedown', 'pointerdown'];
        const handler = async (e) => {
            if (audioInitialized) return;
            // Remove all listeners immediately to prevent double-fire
            events.forEach(ev => document.removeEventListener(ev, handler, true));
            try {
                await initAudio();
                audioInitialized = true;
                bootLog(3, 'Ambient listening activated');
                send({ type: 'boot', status: 'audio_ready', ambient: ambientActive });
            } catch (err) {
                audioInitialized = false;
                console.error('[JARVIS] Audio init failed after gesture:', err);
            }
            resolve();
        };
        events.forEach(ev => document.addEventListener(ev, handler, { capture: true }));
    });
}

// ── AMBIENT VISION — always-on webcam streaming ──────────────────

let visionActive = false;
let visionInterval = null;

function startAmbientVision(videoEl) {
    if (visionActive) return;

    const canvas = document.createElement('canvas');
    canvas.width = 320;
    canvas.height = 240;
    const ctx = canvas.getContext('2d');

    // Send a frame every 2 seconds (low bandwidth, server does the heavy lifting)
    visionInterval = setInterval(() => {
        if (!ws || ws.readyState !== WebSocket.OPEN) return;
        if (videoEl.readyState < 2) return; // video not ready

        ctx.drawImage(videoEl, 0, 0, 320, 240);
        const dataUrl = canvas.toDataURL('image/jpeg', 0.5);
        send({ type: 'video_frame', frame: dataUrl, debug: visionDebugActive });
    }, 2000);

    visionActive = true;
}

function stopAmbientVision() {
    if (visionInterval) clearInterval(visionInterval);
    visionInterval = null;
    visionActive = false;
}

// ── AI VISION (console commands) ─────────────────────────────────

function askVision(prompt) {
    if (ws && ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({ type: 'vision_ask', prompt: prompt || 'What do you see? Describe everything.' }));
    }
}
function enrollFaceId(name) {
    if (ws && ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({ type: 'face_id_enroll', name }));
    }
}

// ── FULLSCREEN ───────────────────────────────────────────────────

const exitFsBtn = document.getElementById('exit-fs');

function updateFsButton() {
    if (exitFsBtn) exitFsBtn.classList.toggle('hidden', !document.fullscreenElement);
}
document.addEventListener('fullscreenchange', updateFsButton);

if (exitFsBtn) {
    exitFsBtn.addEventListener('click', () => {
        if (document.fullscreenElement) document.exitFullscreen();
    });
}

// ── SETTINGS PANEL — AI Providers ─────────────────────────────────

const settingsPanel = document.getElementById('settings-panel');
const settingsClose = document.getElementById('settings-close');
const providerList = document.getElementById('provider-list');
const providerName = document.getElementById('provider-name');
const providerKey = document.getElementById('provider-key');
const providerUrl = document.getElementById('provider-url');
const providerModel = document.getElementById('provider-model');
const providerAddBtn = document.getElementById('provider-add-btn');

function showSettings() {
    settingsPanel.classList.remove('hidden');
    send({ type: 'list_providers' });
}
function hideSettings() { settingsPanel.classList.add('hidden'); }
settingsClose.addEventListener('click', hideSettings);

// Show extra fields for custom providers
providerName.addEventListener('change', () => {
    const isCustom = providerName.value === 'custom';
    providerUrl.style.display = isCustom ? 'block' : 'none';
    providerModel.style.display = isCustom ? 'block' : 'none';
});

providerAddBtn.addEventListener('click', () => {
    const name = providerName.value === 'custom'
        ? (providerUrl.value.includes('localhost') ? 'local' : 'custom')
        : providerName.value;
    const key = providerKey.value.trim();
    if (!name || !key) return;

    send({
        type: 'add_provider',
        name: name,
        api_key: key,
        base_url: providerUrl.value.trim(),
        model: providerModel.value.trim(),
    });

    // Clear form
    providerKey.value = '';
    providerUrl.value = '';
    providerModel.value = '';
    providerName.value = '';
    providerUrl.style.display = 'none';
    providerModel.style.display = 'none';
});

function renderProviders(providers) {
    if (!providers || providers.length === 0) {
        providerList.innerHTML = '<div class="provider-empty">No providers configured. Add one below.</div>';
        return;
    }
    providerList.innerHTML = providers.map(p => `
        <div class="provider-item">
            <div>
                <span class="provider-name">${escapeHtml(p.name)}</span>
                <span class="provider-model">${escapeHtml(p.model || '?')}</span>
            </div>
            <span class="provider-remove" data-name="${escapeAttr(p.name)}">&times;</span>
        </div>
    `).join('');

    // Bind remove buttons
    providerList.querySelectorAll('.provider-remove').forEach(btn => {
        btn.addEventListener('click', () => {
            send({ type: 'remove_provider', name: btn.dataset.name });
        });
    });
}

// ── POWER MENU (JARVIS OS only) ─────────────────────────────────────
(function initPowerMenu() {
    const btn = document.getElementById('power-btn');
    const menu = document.getElementById('power-menu');
    if (!btn || !menu) return;

    // Only show power menu when running as JARVIS OS
    fetch('/api/os').then(r => r.json()).then(data => {
        if (data.is_os) {
            btn.classList.remove('hidden');
        }
    }).catch(() => {});

    btn.addEventListener('click', (e) => {
        e.stopPropagation();
        menu.classList.toggle('hidden');
    });

    document.addEventListener('click', () => menu.classList.add('hidden'));
    menu.addEventListener('click', (e) => e.stopPropagation());

    menu.querySelectorAll('.power-option').forEach(option => {
        option.addEventListener('click', async () => {
            const action = option.dataset.action;
            const labels = {
                shutdown: 'Shutting down...',
                reboot: 'Rebooting...',
                sleep: 'Going to sleep...'
            };

            // Confirm dangerous actions
            if (action === 'shutdown') {
                if (!confirm('Shutdown JARVIS OS?')) return;
            }

            menu.classList.add('hidden');
            btn.style.color = '#ff8800';
            btn.title = labels[action] || 'Processing...';

            try {
                await fetch('/api/power', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ action })
                });
            } catch (e) {
                // Server may already be going down
            }
        });
    });
})();

// ── BOOT ──────────────────────────────────────────────────────────
boot();

// ── CHAT PANEL ──────────────────────────────────────────────

const chatPanel = document.getElementById('chat-panel');
const chatToggle = document.getElementById('chat-toggle');
const chatClose = document.getElementById('chat-close');
const chatMinimize = document.getElementById('chat-minimize');
const chatInput = document.getElementById('chat-input');
const chatSend = document.getElementById('chat-send');
const chatVoice = document.getElementById('chat-voice');
const chatMessages = document.getElementById('chat-messages');
const neuralLine = document.getElementById('neural-line');
const neuralPulse = document.querySelector('.neural-pulse');
const neuralSvg = document.getElementById('neural-link');

let chatOpen = false;

function toggleChat() {
    chatOpen = !chatOpen;
    if (chatOpen) {
        chatPanel.classList.remove('hidden');
        neuralSvg.style.display = 'block';
        chatInput.focus();
        updateNeuralLink();
    } else {
        chatPanel.classList.add('hidden');
        neuralSvg.style.display = 'none';
    }
}

function updateNeuralLink() {
    if (!chatOpen) return;
    // Draw line from reactor center to chat panel top-left
    const reactor = document.getElementById('reactor');
    if (!reactor) return;
    const rRect = reactor.getBoundingClientRect();
    const cRect = chatPanel.getBoundingClientRect();
    
    const x1 = rRect.left + rRect.width / 2;
    const y1 = rRect.top + rRect.height / 2;
    const x2 = cRect.left + 20;
    const y2 = cRect.top;
    
    neuralLine.setAttribute('x1', x1);
    neuralLine.setAttribute('y1', y1);
    neuralLine.setAttribute('x2', x2);
    neuralLine.setAttribute('y2', y2);
    
    // Animate pulse along the line
    if (neuralPulse) {
        neuralPulse.setAttribute('cx', x1);
        neuralPulse.setAttribute('cy', y1);
    }
}

function addChatMessage(role, text) {
    const msg = document.createElement('div');
    msg.className = `chat-msg ${role}`;
    msg.innerHTML = `
        <span class="msg-label">${role === 'user' ? 'YOU' : 'JARVIS'}</span>
        <span class="msg-text">${text}</span>
    `;
    chatMessages.appendChild(msg);
    chatMessages.scrollTop = chatMessages.scrollHeight;
    return msg;
}

function addThinking() {
    const msg = document.createElement('div');
    msg.className = 'chat-msg jarvis';
    msg.id = 'thinking-msg';
    msg.innerHTML = `
        <span class="msg-label">JARVIS</span>
        <span class="msg-text msg-thinking">Thinking...</span>
    `;
    chatMessages.appendChild(msg);
    chatMessages.scrollTop = chatMessages.scrollHeight;
    return msg;
}

function removeThinking() {
    const el = document.getElementById('thinking-msg');
    if (el) el.remove();
}

async function sendChatMessage() {
    const text = chatInput.value.trim();
    if (!text) return;
    
    chatInput.value = '';
    addChatMessage('user', text);
    addThinking();
    
    try {
        // Send via WebSocket or HTTP API
        const response = await fetch('/api/think', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ text: text }),
        });
        const data = await response.json();
        removeThinking();
        addChatMessage('jarvis', data.response || data.content || 'No response.');
    } catch (err) {
        removeThinking();
        // Try WebSocket fallback
        if (ws && ws.readyState === WebSocket.OPEN) {
            ws.send(JSON.stringify({ type: 'query', text: text }));
            // Response will come via WS message handler
        } else {
            addChatMessage('jarvis', 'Connection error. Check if JARVIS server is running.');
        }
    }
    
    updateNeuralLink();
}

// Event listeners
if (chatToggle) chatToggle.addEventListener('click', toggleChat);
if (chatClose) chatClose.addEventListener('click', toggleChat);
if (chatMinimize) chatMinimize.addEventListener('click', toggleChat);
if (chatSend) chatSend.addEventListener('click', sendChatMessage);
if (chatInput) {
    chatInput.addEventListener('keydown', (e) => {
        if (e.key === 'Enter' && !e.shiftKey) {
            e.preventDefault();
            sendChatMessage();
        }
    });
}

// Keyboard shortcut: C to toggle chat
document.addEventListener('keydown', (e) => {
    if (e.key === 'c' && !e.ctrlKey && !e.altKey && document.activeElement !== chatInput) {
        toggleChat();
    }
    if (e.key === 'Escape' && chatOpen) {
        toggleChat();
    }
});

// Handle WS responses for chat
const origOnMessage = ws ? ws.onmessage : null;
function handleChatWsMessage(event) {
    try {
        const data = JSON.parse(event.data);
        if (data.type === 'message' && chatOpen) {
            removeThinking();
            addChatMessage('jarvis', data.content || '');
        }
    } catch (e) {}
    // Call original handler too
    if (origOnMessage) origOnMessage(event);
}

// Update neural link on resize
window.addEventListener('resize', updateNeuralLink);
setInterval(updateNeuralLink, 2000);

// Start with neural link hidden
if (neuralSvg) neuralSvg.style.display = 'none';

