package com.jarvis.android.presentation.chat

import androidx.compose.foundation.Canvas
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableFloatStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberUpdatedState
import androidx.compose.runtime.setValue
import androidx.compose.runtime.withFrameMillis
import androidx.compose.ui.Modifier
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.Color
import kotlinx.coroutines.isActive
import kotlin.math.PI
import kotlin.math.acos
import kotlin.math.cos
import kotlin.math.sin
import kotlin.math.sqrt
import kotlin.random.Random

// ──────────────────────────────────────────────────────────────────────────────
// ParticleSphere — organic glowing particle cloud, pure Compose Canvas.
//
// ## Visual technique
// Each of the [P_COUNT] particles is drawn as THREE concentric circles:
//   1. Outer bloom  — radius × 8,  alpha ~4 %  → large soft halo
//   2. Mid glow     — radius × 3,  alpha ~15 % → filled glow body
//   3. Bright core  — radius × 1,  alpha ~90 % → sharp bright dot
// When 100+ particles overlap, their halos stack and create the dense,
// soft "energy sphere" look without GPU shaders or BlurMaskFilter.
//
// ## Particle placement
// Points are randomly distributed in a spherical shell [0.55–1.0] × radius
// using uniform spherical coordinates (random elevation via acos, random
// azimuth uniform in [0, 2π]).  This gives a natural cluster without the
// pole-clumping of naive random angles, and without the mathematical regularity
// of the Fibonacci lattice.
//
// ## Motion
// • A [LaunchedEffect] loop rotates the cloud Y-axis each vsync.
//   Speed: 0.15 rad/s idle → 0.55 rad/s when [isAiSpeaking].
// • Each particle has an independent drift phase/speed.  Its position
//   is offset each frame by a small sine: `sin(time·speed + phase) × 0.04`.
//   This makes every particle float independently — no two move the same.
//
// ## Audio reactivity
// Each particle maps to one of 28 audio bars.  Bar amplitude → radial jitter
// and brightness boost, so the sphere surface visibly pulses with the voice.
// ──────────────────────────────────────────────────────────────────────────────

@Composable
fun ParticleSphere(
    amplitude:    Float,        // 0→1 smooth, from animateFloatAsState
    isAiSpeaking: Boolean,
    audioAmps:    FloatArray,   // 28-element RMS from AudioAmplitudeMonitor
    modifier:     Modifier = Modifier,
) {
    // ── Per-instance buffers — no per-frame heap allocation ───────────────
    val buf = remember {
        object {
            val projX    = FloatArray(P_COUNT)
            val projY    = FloatArray(P_COUNT)
            val projZ    = FloatArray(P_COUNT)
            val projSz   = FloatArray(P_COUNT)
            val projGlow = FloatArray(P_COUNT)
            val sortIdx  = IntArray(P_COUNT) { it }
        }
    }

    // Y-rotation driven by frame loop
    var rotY by remember { mutableFloatStateOf(0f) }
    val latestSpeaking = rememberUpdatedState(isAiSpeaking)

    LaunchedEffect(Unit) {
        var prevMs = 0L
        while (isActive) {
            withFrameMillis { ms ->
                if (prevMs != 0L) {
                    val dt    = (ms - prevMs) / 1_000f
                    val speed = if (latestSpeaking.value) 0.55f else 0.15f
                    rotY += dt * speed
                }
                prevMs = ms
            }
        }
    }

    Canvas(modifier = modifier) {
        val cx     = size.width  / 2f
        val cy     = size.height / 2f
        // Sphere breathes slightly when AI speaks
        val radius = minOf(size.width, size.height) * 0.44f * (1f + amplitude * 0.08f)
        val focal  = radius * FOCAL

        val cosY = cos(rotY)
        val sinY = sin(rotY)
        // Re-use rotY as the drift clock so particles move without extra state
        val drift = rotY

        // ── Project ───────────────────────────────────────────────────────
        for (i in 0 until P_COUNT) {
            // Per-particle drift offset (each particle floats independently)
            val driftAmt = sin(drift * Geo.driftSpeed[i] + Geo.driftPhase[i]) * 0.04f
            val dDx      = cos(Geo.driftPhase[i])         * driftAmt
            val dDy      = sin(Geo.driftPhase[i] * 1.37f) * driftAmt

            val x0 = Geo.px[i] + dDx
            val y0 = Geo.py[i] + dDy
            val z0 = Geo.pz[i]

            // Y-axis spin
            val x1 =  x0 * cosY + z0 * sinY
            val z1 = -x0 * sinY + z0 * cosY
            // Fixed tilt so 3-D depth shows at every rotation angle
            val y2 =  y0 * TILT_COS - z1 * TILT_SIN
            val z2 =  y0 * TILT_SIN + z1 * TILT_COS
            val x2 = x1

            // Audio radial jitter
            val barAmp = if (isAiSpeaking) audioAmps[Geo.barIdx[i]] else 0f
            val r      = radius * (1f + barAmp * 0.15f)

            // Perspective divide
            val persp      = focal / (focal + z2 * radius)
            buf.projX[i]   = cx + x2 * r * persp
            buf.projY[i]   = cy + y2 * r * persp
            buf.projZ[i]   = z2
            buf.projSz[i]  = Geo.baseSize[i] * persp

            // Depth-based brightness: back (z≈-1) dim, front (z≈+1) bright
            val depth      = ((z2 + 1f) / 2f).coerceIn(0f, 1f)
            val baseGlow   = 0.05f + depth * 0.90f
            val audioBoost = if (isAiSpeaking) barAmp * 0.50f else 0f
            // Particles brighten further as amplitude rises
            buf.projGlow[i] = (baseGlow * (0.40f + amplitude * 0.60f) + audioBoost)
                .coerceIn(0f, 1f)
        }

        // ── Depth sort: insertion sort (O(n) for nearly-sorted) ───────────
        for (i in 0 until P_COUNT) buf.sortIdx[i] = i
        for (i in 1 until P_COUNT) {
            val key = buf.sortIdx[i]
            val kz  = buf.projZ[key]
            var j   = i - 1
            while (j >= 0 && buf.projZ[buf.sortIdx[j]] > kz) {
                buf.sortIdx[j + 1] = buf.sortIdx[j]
                j--
            }
            buf.sortIdx[j + 1] = key
        }

        // ── Draw back → front (painter's algorithm) ───────────────────────
        for (si in 0 until P_COUNT) {
            val i    = buf.sortIdx[si]
            val glow = buf.projGlow[i]
            val cT   = Geo.colorTemp[i]
            val pos  = Offset(buf.projX[i], buf.projY[i])
            val sz   = buf.projSz[i].coerceAtLeast(1.0f)

            // Color: deep blue (#1040C0) → bright pale blue (#A8CFFF)
            val cr = DEEP_R + (LITE_R - DEEP_R) * cT
            val cg = DEEP_G + (LITE_G - DEEP_G) * cT
            val cb = DEEP_B + (LITE_B - DEEP_B) * cT

            // Layer 1 — large outer bloom (halos stack across particles → dense glow)
            drawCircle(
                color  = Color(cr, cg, cb, (glow * 0.045f).coerceAtMost(0.09f)),
                radius = sz * 8f,
                center = pos,
            )
            // Layer 2 — mid glow body
            drawCircle(
                color  = Color(cr, cg, cb, (glow * 0.15f).coerceAtMost(0.28f)),
                radius = sz * 3f,
                center = pos,
            )
            // Layer 3 — bright core dot
            drawCircle(
                color  = Color(cr, cg, cb, glow.coerceAtMost(0.92f)),
                radius = sz,
                center = pos,
            )
        }
    }
}

// ── Constants ─────────────────────────────────────────────────────────────────

private const val P_COUNT  = 160
private const val FOCAL    = 2.8f       // perspective strength
private const val TILT     = 0.32f      // fixed X-tilt (radians)

private val TILT_COS = cos(TILT)
private val TILT_SIN = sin(TILT)

// Color range — deep blue to pale blue-white
private val DEEP  = Color(0xFF0F3DBF)
private val LITE  = Color(0xFFA8CFFF)
private val DEEP_R = DEEP.red;  private val DEEP_G = DEEP.green;  private val DEEP_B = DEEP.blue
private val LITE_R = LITE.red;  private val LITE_G = LITE.green;  private val LITE_B = LITE.blue

// ── Geometry — random spherical shell, computed once ─────────────────────────

private object Geo {
    val px         = FloatArray(P_COUNT)
    val py         = FloatArray(P_COUNT)
    val pz         = FloatArray(P_COUNT)
    /** Each particle drifts on its own sine path (phase + speed). */
    val driftPhase = FloatArray(P_COUNT)
    val driftSpeed = FloatArray(P_COUNT)
    /** Base radius in local units (varied for organic size distribution). */
    val baseSize   = FloatArray(P_COUNT)
    /** 0 = deep-blue, 1 = bright pale-blue (color temperature). */
    val colorTemp  = FloatArray(P_COUNT)
    /** Which of the 28 audio bars drives this particle's jitter. */
    val barIdx     = IntArray(P_COUNT)

    init {
        // Fixed seed → same layout every time (no jitter on first frame).
        val rng  = Random(1337)
        val bars = AudioAmplitudeMonitor.BAR_COUNT   // 28

        for (i in 0 until P_COUNT) {
            // Uniform random point on sphere surface (Marsaglia-style):
            //   elevation = acos(2U−1) gives uniform lat distribution.
            //   azimuth   = 2πU gives uniform lon distribution.
            val elevation = acos(2f * rng.nextFloat() - 1f)  // [0, π]
            val azimuth   = rng.nextFloat() * 2f * PI.toFloat()

            // Shell radius: 55–100 % of sphere radius.
            // More particles concentrated near the surface (outer 30 %).
            val shellR = if (rng.nextFloat() > 0.35f)
                0.72f + rng.nextFloat() * 0.28f   // outer shell: 72–100 %
            else
                0.55f + rng.nextFloat() * 0.17f   // inner haze:  55–72 %

            px[i] = shellR * sin(elevation) * cos(azimuth)
            py[i] = shellR * sin(elevation) * sin(azimuth)
            pz[i] = shellR * cos(elevation)

            driftPhase[i] = rng.nextFloat() * 2f * PI.toFloat()
            driftSpeed[i] = 0.25f + rng.nextFloat() * 0.55f

            // Size: most particles small (1.2–2.5 px), a few large highlight orbs
            baseSize[i] = if (rng.nextFloat() > 0.82f)
                2.8f + rng.nextFloat() * 2.2f   // large bright highlights
            else
                1.2f + rng.nextFloat() * 1.3f   // common small particles

            // Color temperature: most cool-blue, some bright-white highlights
            colorTemp[i] = if (rng.nextFloat() > 0.72f)
                0.55f + rng.nextFloat() * 0.45f  // bright
            else
                rng.nextFloat() * 0.38f           // dim/cool

            barIdx[i] = (i * bars / P_COUNT).coerceIn(0, bars - 1)
        }
    }
}
