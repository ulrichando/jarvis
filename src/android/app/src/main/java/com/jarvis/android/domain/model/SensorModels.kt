package com.jarvis.android.domain.model

// ── Sensor reading ────────────────────────────────────────────────────────────

data class SensorReading(
    val sensorType:  Int,
    val sensorName:  String,
    val values:      FloatArray,
    val accuracy:    Int,
    val timestampNs: Long,
) {
    /** Convenience accessor for single-value sensors (light, pressure, etc.). */
    val value: Float get() = values.firstOrNull() ?: 0f
}

// ── Sensor info ───────────────────────────────────────────────────────────────

data class SensorInfo(
    val type:         Int,
    val name:         String,
    val vendor:       String,
    val version:      Int,
    val maxRange:     Float,
    val resolution:   Float,
    val power:        Float,   // mA
    val minDelayUs:   Int,
)

// ── Location reading ──────────────────────────────────────────────────────────

data class LocationReading(
    val latitudeDeg:  Double,
    val longitudeDeg: Double,
    val altitudeM:    Double,
    val accuracyM:    Float,
    val speedMps:     Float,
    val bearingDeg:   Float,
    val timestampMs:  Long,
    val provider:     String,
)

// ── Orientation ───────────────────────────────────────────────────────────────

data class OrientationReading(
    val azimuthDeg: Float,   // 0 = north
    val pitchDeg:   Float,
    val rollDeg:    Float,
)
