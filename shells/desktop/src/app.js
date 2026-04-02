// J.A.R.V.I.S. MARK IV — Fixed Interrupt, CLI Client Ready

const WS_URL = `ws://localhost:8765/ws`;

let ws = null;
let isSpeaking = false;

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

// HUD
let panelIndex = 0;
const PANELS = ['hud-right', 'hud-left', 'hud-bottom', 'hud-top'];
const MAX_CARDS = 4;
let currentMode = 'normal';

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
    if (e.key === 'Escape') { closeMedia(); stopSpeaking(); }
});
fallbackInput.addEventListener('keydown', (e) => { if (e.key === 'Enter') { const t = fallbackInput.value.trim(); if (t) { fallbackInput.value = ''; sendQuery(t); } } });

// Click anywhere: stop JARVIS talking, or enable mic (first click grants permission)
document.addEventListener('click', (e) => {
    if (e.target.closest('#media-overlay') || e.target.closest('.hud-card') ||
        e.target === fallbackInput) return;
    if (isSpeaking) {
        stopSpeaking();
    } else if (!audioContext) {
        // First click — request mic permission and start ambient listening
        initAudio();
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
    if (type === 'time') {
        const now = new Date();
        mediaContent.innerHTML = `<div class="media-time">${now.toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit', hour12: true })}<div class="date">${now.toLocaleDateString('en-US', { weekday: 'long', year: 'numeric', month: 'long', day: 'numeric' })}</div></div>`;
        setTimeout(closeMedia, 5000);
    } else if (type === 'image') {
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
    try {
        audioContext = new (window.AudioContext || window.webkitAudioContext)({ sampleRate: 16000 });
        const stream = await navigator.mediaDevices.getUserMedia({
            audio: { channelCount: 1, sampleRate: 16000, echoCancellation: true, noiseSuppression: true }
        });
        micStream = stream;
        const source = audioContext.createMediaStreamSource(stream);
        micAnalyser = audioContext.createAnalyser();
        micAnalyser.fftSize = 256;
        micAnalyser.smoothingTimeConstant = 0.7;
        source.connect(micAnalyser);
        micDataArray = new Uint8Array(micAnalyser.frequencyBinCount);
        readMicLevel();

        // Auto-start ambient listening — JARVIS is always ready
        startAmbientListening();
    } catch (e) {
        console.error('[JARVIS] Microphone init failed:', e);
    }
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

function connect() {
    ws = new WebSocket(WS_URL);
    ws.onopen = () => {};
    ws.binaryType = 'arraybuffer';
    ws.onmessage = (e) => {
        if (e.data instanceof ArrayBuffer) return; // ignore binary echoes
        try {
            const data = JSON.parse(e.data);
            if (data.type === 'stt_result') {
                updateMicUI(false);
                sendQuery(data.text);
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
    ws.onclose = () => { setTimeout(connect, 3000); };
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
        // Always show the response visually
        addCard('response', content);
        if (currentMode === 'cli' || currentMode === 'berbon') {
            // CLI/berbon: only speak very short clean responses
            if (spoken.length > 0 && spoken.length < 100) speakResponse(spoken, voiceStyle);
        } else {
            // Normal: speak the cleaned version
            if (spoken.length > 0) speakResponse(spoken, voiceStyle);
        }
    } else if (data.type === 'status' && data.status === 'thinking') {
        setReactorState('thinking');
    } else if (data.type === 'suggestion') {
        addCard('suggestion', data.content);
    }
}

function checkMediaCommand(text) {
    const lower = text.toLowerCase();
    if (lower.includes('[show:time]') || lower.includes('[display:time]')) {
        showMedia('time');
        const clean = text.replace(/\[show:time\]|\[display:time\]/gi, '').trim();
        if (clean) speakResponse(clean);
        return true;
    }
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

    try {
        // Create a ScriptProcessorNode to capture raw audio
        // Buffer size 4096 at 16kHz ≈ 256ms chunks
        const source = audioContext.createMediaStreamSource(micStream);
        ambientProcessor = audioContext.createScriptProcessor(4096, 1, 1);

        ambientProcessor.onaudioprocess = (event) => {
            // Don't stream while JARVIS is speaking (prevents echo)
            if (isSpeaking) return;
            if (!ws || ws.readyState !== WebSocket.OPEN) return;

            // Get raw PCM float32 data
            const input = event.inputBuffer.getChannelData(0);

            // Downsample from audioContext.sampleRate to 16000Hz
            const ratio = audioContext.sampleRate / 16000;
            const outputLength = Math.floor(input.length / ratio);
            const output = new Float32Array(outputLength);
            for (let i = 0; i < outputLength; i++) {
                output[i] = input[Math.floor(i * ratio)];
            }

            // Send raw PCM to server as binary
            ws.send(output.buffer);
        };

        source.connect(ambientProcessor);
        ambientProcessor.connect(audioContext.destination);
        ambientActive = true;

        // Update UI — show subtle listening indicator
        updateMicUI(true, '');
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
    const btn = document.getElementById('mic-btn');
    const status = document.getElementById('mic-status');
    if (btn) {
        if (listening) {
            btn.classList.add('recording');
            btn.title = 'JARVIS is listening';
        } else {
            btn.classList.remove('recording');
            btn.title = 'Click to enable listening';
        }
    }
    if (status) status.textContent = statusText || '';
}

// ── TTS — Chunked speech with natural pauses ─────────────────────
//
// JARVIS speaks in chunks — like a human taking breaths between thoughts.
// Each chunk is a sentence or phrase. Between chunks, silence.
// This makes speech feel alive, not robotic.

let currentAudio = null;
let speechQueue = [];        // Queue of chunks waiting to be spoken
let speakingChunk = false;   // Currently playing a chunk
let speechAborted = false;   // User interrupted
let currentVoiceStyle = 'default';

function speakResponse(text, voiceStyle) {
    // Aggressive cleaning — remove ANYTHING that isn't natural speech
    let cleanText = text
        .replace(/\[show:\w+\]/gi, '')
        .replace(/\[\/show\]/gi, '')
        .replace(/\[run:.*?\]/gi, '')
        .replace(/\[display:\w+\]/gi, '')
        .replace(/```[\s\S]*?```/g, '')
        .replace(/`[^`]+`/g, '')
        .replace(/https?:\/\/\S+/g, '')
        .replace(/\/[\w\/\.\-]+/g, ' ')
        .replace(/\s*-{2,}\w[\w-]*/g, '')
        // Terminal output patterns
        .replace(/^[\s]*[\$#>].*$/gm, '')
        .replace(/drwx.*$/gm, '')
        .replace(/-rw[r-].*$/gm, '')
        .replace(/total \d+/g, '')
        .replace(/\[Plugin\].*$/gm, '')
        .replace(/Traceback.*$/gm, '')
        .replace(/File ".*$/gm, '')
        .replace(/^\s*\d+\.\d+\.\d+\.\d+.*$/gm, '')
        // Code patterns
        .replace(/[{}()\[\];=<>|\\]/g, '')
        .replace(/^\s*(import|from|def |class |if |for |while |return |print)\b.*$/gm, '')
        .replace(/\w+\.\w+\.\w+/g, '')  // dotted.module.paths
        // Cleanup
        .replace(/\n+/g, ' ')
        .replace(/\s{2,}/g, ' ')
        .trim();
    if (!cleanText || cleanText.length < 2) return;

    stopSpeaking();
    currentVoiceStyle = voiceStyle || 'default';
    speechAborted = false;

    // Request chunked speech plan from server
    fetch(`http://localhost:8765/tts/chunks?text=${encodeURIComponent(cleanText)}&style=${currentVoiceStyle}`)
        .then(r => r.json())
        .then(data => {
            if (speechAborted) return;
            const chunks = data.chunks || [];
            if (chunks.length === 0) return;

            // If only one short chunk, speak directly (no chunking overhead)
            if (chunks.length === 1) {
                _speakSingle(cleanText);
                return;
            }

            // Queue up chunks and start playing
            speechQueue = chunks.map(c => ({
                text: c.text,
                pauseAfter: c.pause_after_ms || 0,
                important: c.is_important || false,
            }));
            _playNextChunk();
        })
        .catch(() => {
            // Chunking failed — fall back to single-shot speech
            _speakSingle(cleanText);
        });
}

function _playNextChunk() {
    if (speechAborted || speechQueue.length === 0) {
        // All chunks done — wrap up
        isSpeaking = false;
        speakingChunk = false;
        stopTTSPulse();
        setReactorState('');
        currentAudio = null;
        return;
    }

    const chunk = speechQueue.shift();
    speakingChunk = true;

    // Build TTS URL with SSML style
    const url = `/tts?text=${encodeURIComponent(chunk.text)}&style=${currentVoiceStyle}`;
    currentAudio = new Audio(url);

    currentAudio.onplay = () => {
        isSpeaking = true;
        setReactorState('speaking');
        startTTSPulse();
    };

    currentAudio.onended = () => {
        speakingChunk = false;
        currentAudio = null;

        if (speechAborted) {
            _finishSpeaking();
            return;
        }

        // Natural pause between chunks — this is where JARVIS "breathes"
        if (chunk.pauseAfter > 0 && speechQueue.length > 0) {
            // During the pause, reduce the TTS pulse to simulate silence
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
        // Skip this chunk, try next
        if (!speechAborted && speechQueue.length > 0) {
            _playNextChunk();
        } else {
            _finishSpeaking();
        }
    };

    currentAudio.play().catch(() => {
        speakingChunk = false;
        _finishSpeaking();
    });
}

function _speakSingle(text) {
    // Single-shot speech — used for short responses or fallback
    const url = `/tts?text=${encodeURIComponent(text)}&style=${currentVoiceStyle}`;
    currentAudio = new Audio(url);

    currentAudio.onplay = () => {
        isSpeaking = true;
        setReactorState('speaking');
        startTTSPulse();
    };

    currentAudio.onended = () => { _finishSpeaking(); };
    currentAudio.onerror = () => { _finishSpeaking(); };
    currentAudio.play().catch(() => { _finishSpeaking(); });
}

function _finishSpeaking() {
    isSpeaking = false;
    speakingChunk = false;
    stopTTSPulse();
    setReactorState('');
    currentAudio = null;
    speechQueue = [];
}

function stopSpeaking() {
    speechAborted = true;
    speechQueue = [];
    if (currentAudio) {
        currentAudio.pause();
        currentAudio.currentTime = 0;
        currentAudio = null;
    }
    if (window.speechSynthesis) speechSynthesis.cancel();
    isSpeaking = false;
    speakingChunk = false;
    stopTTSPulse();
    setReactorState('');
}

// ── UTILS ─────────────────────────────────────────────────────────

function escapeHtml(t) { const d = document.createElement('div'); d.textContent = t; return d.innerHTML; }
function escapeAttr(t) { return t.replace(/"/g, '&quot;').replace(/'/g, '&#39;'); }

// ── INIT ──────────────────────────────────────────────────────────

initAudio();
connect();

// Wire up mic button
const micBtn = document.getElementById('mic-btn');
if (micBtn) {
    micBtn.addEventListener('click', (e) => {
        e.stopPropagation();
        if (isSpeaking) { stopSpeaking(); return; }
        if (!audioContext) { initAudio().then(() => startRecording()); return; }
        if (isRecording) stopRecording();
        else startRecording();
    });
}
