// Lightweight speaker fingerprint — runs on the Float32Array buffer that
// Silero VAD already hands us at end of utterance. Not a neural speaker
// model; a 24-dimensional acoustic summary good enough to tell Ulrich
// apart from children, women, TV hosts, and podcast guests. Can be
// replaced with ECAPA-TDNN (or similar) ONNX model later without
// touching the call site in useSpeech.js.

import { useCallback, useEffect, useMemo, useRef } from 'react'

const STORAGE_KEY    = 'jarvis.speakerFingerprint.v1'
const ENROLL_COUNT   = 3
const MEL_BINS       = 16
const EXTRA_FEATURES = 8   // F0, ZCR, energy stats, spectral tilt, centroid…
const FINGERPRINT_DIM = MEL_BINS + EXTRA_FEATURES

// Compute a deterministic spectral fingerprint for a 16 kHz mono buffer.
function computeFingerprint(floats, sampleRate = 16000) {
  if (!floats || floats.length < sampleRate * 0.3) {
    return new Float32Array(FINGERPRINT_DIM)  // too short — zeros
  }

  const frameSize = 1024
  const hop = 512
  const out = new Float32Array(FINGERPRINT_DIM)
  const melBins = new Float32Array(MEL_BINS)
  let frames = 0
  let totalEnergy = 0
  let energyMax = 0
  let zcrSum = 0
  let centroidSum = 0
  let tiltSum = 0

  // Simple Hanning window
  const window = new Float32Array(frameSize)
  for (let i = 0; i < frameSize; i++) {
    window[i] = 0.5 * (1 - Math.cos((2 * Math.PI * i) / (frameSize - 1)))
  }

  // Naive goertzel-style magnitudes at log-spaced freq bands.
  const freqEdges = logspace(80, 7500, MEL_BINS + 1)

  for (let start = 0; start + frameSize <= floats.length; start += hop) {
    frames++

    // Zero-crossing rate
    let zc = 0
    for (let i = 1; i < frameSize; i++) {
      if ((floats[start + i - 1] < 0) !== (floats[start + i] < 0)) zc++
    }
    zcrSum += zc / frameSize

    // Windowed frame energy
    let eSum = 0
    for (let i = 0; i < frameSize; i++) {
      const s = floats[start + i] * window[i]
      eSum += s * s
    }
    const energy = Math.sqrt(eSum / frameSize)
    totalEnergy += energy
    if (energy > energyMax) energyMax = energy

    // Band energies via goertzel — cheap, no FFT dep needed.
    const bandEnergies = new Float32Array(MEL_BINS)
    let totalMag = 0
    for (let b = 0; b < MEL_BINS; b++) {
      const fc = (freqEdges[b] + freqEdges[b + 1]) / 2
      const w = (2 * Math.PI * fc) / sampleRate
      const coeff = 2 * Math.cos(w)
      let s0 = 0, s1 = 0, s2 = 0
      for (let i = 0; i < frameSize; i++) {
        s0 = floats[start + i] * window[i] + coeff * s1 - s2
        s2 = s1; s1 = s0
      }
      const mag = Math.sqrt(s1 * s1 + s2 * s2 - coeff * s1 * s2)
      bandEnergies[b] = mag
      totalMag += mag
      melBins[b] += Math.log(1e-6 + mag)
    }

    // Spectral centroid (in Hz, normalized)
    let wsum = 0
    for (let b = 0; b < MEL_BINS; b++) {
      const fc = (freqEdges[b] + freqEdges[b + 1]) / 2
      wsum += fc * bandEnergies[b]
    }
    centroidSum += totalMag > 0 ? wsum / totalMag : 0

    // Spectral tilt — log-log slope between first and second halves
    let lowSum = 0, hiSum = 0
    for (let b = 0; b < MEL_BINS / 2; b++) lowSum += bandEnergies[b]
    for (let b = MEL_BINS / 2; b < MEL_BINS; b++) hiSum += bandEnergies[b]
    tiltSum += Math.log((lowSum + 1e-6) / (hiSum + 1e-6))
  }

  if (frames === 0) return out

  // Fill the fingerprint vector
  for (let b = 0; b < MEL_BINS; b++) out[b] = melBins[b] / frames
  out[MEL_BINS + 0] = zcrSum / frames
  out[MEL_BINS + 1] = totalEnergy / frames
  out[MEL_BINS + 2] = energyMax
  out[MEL_BINS + 3] = (centroidSum / frames) / 8000   // normalized 0–1
  out[MEL_BINS + 4] = tiltSum / frames
  out[MEL_BINS + 5] = frames
  // Cheap autocorrelation-based F0 estimate over first voiced frame.
  out[MEL_BINS + 6] = estimateF0(floats, sampleRate) / 400
  out[MEL_BINS + 7] = floats.length / sampleRate  // duration in seconds

  // L2 normalize the mel-bin portion for stable cosine comparison
  let norm = 0
  for (let b = 0; b < MEL_BINS; b++) norm += out[b] * out[b]
  norm = Math.sqrt(norm) || 1
  for (let b = 0; b < MEL_BINS; b++) out[b] /= norm

  return out
}

function logspace(lo, hi, n) {
  const arr = new Float32Array(n)
  const logLo = Math.log(lo), logHi = Math.log(hi)
  for (let i = 0; i < n; i++) {
    arr[i] = Math.exp(logLo + ((logHi - logLo) * i) / (n - 1))
  }
  return arr
}

function estimateF0(floats, sampleRate) {
  // Autocorrelation peak in the 80–400 Hz range, single frame from middle.
  const mid = Math.floor(floats.length / 2)
  const len = Math.min(2048, floats.length - mid)
  if (len < 512) return 0
  const minLag = Math.floor(sampleRate / 400)
  const maxLag = Math.floor(sampleRate / 80)
  let best = 0, bestLag = 0
  for (let lag = minLag; lag <= maxLag && lag < len; lag++) {
    let s = 0
    for (let i = 0; i + lag < len; i++) s += floats[mid + i] * floats[mid + i + lag]
    if (s > best) { best = s; bestLag = lag }
  }
  return bestLag > 0 ? sampleRate / bestLag : 0
}

function cosineSim(a, b) {
  let dot = 0, na = 0, nb = 0
  for (let i = 0; i < a.length; i++) { dot += a[i] * b[i]; na += a[i] * a[i]; nb += b[i] * b[i] }
  const denom = Math.sqrt(na * nb) || 1
  return Math.max(0, Math.min(1, (dot / denom + 1) / 2))  // remap [-1,1]→[0,1]
}

function loadFingerprint() {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    if (!raw) return null
    const parsed = JSON.parse(raw)
    if (!parsed?.fingerprint || parsed.fingerprint.length !== FINGERPRINT_DIM) return null
    return parsed
  } catch { return null }
}

function saveFingerprint(data) {
  try { localStorage.setItem(STORAGE_KEY, JSON.stringify(data)) } catch {}
}

// Hook — returns { scoreUtterance, enrolledCount, reset }.
export default function useSpeakerId() {
  const enrolledRef = useRef(loadFingerprint())
  const pendingRef  = useRef([])  // accumulates fingerprints during enrollment

  const scoreUtterance = useCallback((floats) => {
    const fp = computeFingerprint(floats)

    // Enrollment phase — accumulate until we have ENROLL_COUNT samples.
    if (!enrolledRef.current) {
      pendingRef.current.push(Array.from(fp))
      if (pendingRef.current.length >= ENROLL_COUNT) {
        // Average the samples → fingerprint.
        const mean = new Float32Array(FINGERPRINT_DIM)
        for (const v of pendingRef.current) {
          for (let i = 0; i < FINGERPRINT_DIM; i++) mean[i] += v[i]
        }
        for (let i = 0; i < FINGERPRINT_DIM; i++) mean[i] /= pendingRef.current.length
        enrolledRef.current = {
          fingerprint: Array.from(mean),
          enrolledAt: Date.now(),
          samples: pendingRef.current.length,
        }
        saveFingerprint(enrolledRef.current)
        pendingRef.current = []
        console.log('[speakerId] enrollment complete')
      }
      // During enrollment, trust the speaker fully.
      return { confidence: 1.0, phase: 'enrolling', fingerprint: fp }
    }

    const reference = new Float32Array(enrolledRef.current.fingerprint)
    const confidence = cosineSim(fp, reference)
    return { confidence, phase: 'verifying', fingerprint: fp }
  }, [])

  const reset = useCallback(() => {
    try { localStorage.removeItem(STORAGE_KEY) } catch {}
    enrolledRef.current = null
    pendingRef.current = []
    console.log('[speakerId] fingerprint reset — will re-enroll over next ' + ENROLL_COUNT + ' turns')
  }, [])

  useEffect(() => {
    // Expose a reset for manual re-enrollment if voice quality drifts.
    window.__jarvisResetSpeakerId = reset
    return () => { delete window.__jarvisResetSpeakerId }
  }, [reset])

  // Stabilise the returned object across renders. Without useMemo this
  // hook returned a fresh `{}` every render, which cascaded through
  // useSpeech.js: that object was a useCallback dep of `startVad`, so
  // `startVad` got a new identity every render, so the
  // `useEffect([muted, startVad, stopVad])` fired continuously, tearing
  // down and re-creating Silero VAD roughly every 60 ms (driven by the
  // audio-level timer's setState re-renders). Symptom was "mic keeps
  // breaking in and out" — Silero never reached steady state. Keep
  // this memoised. Counts are derived from refs, so they're stable per
  // render in the parent's view.
  return useMemo(() => ({
    scoreUtterance,
    enrolledCount: enrolledRef.current ? 1 : pendingRef.current.length,
    isEnrolled: !!enrolledRef.current,
    reset,
  }), [scoreUtterance, reset])
}
