package com.jarvis.android.data.repository

import android.Manifest
import android.content.Context
import android.content.pm.PackageManager
import android.hardware.Sensor
import android.hardware.SensorEvent
import android.hardware.SensorEventListener
import android.hardware.SensorManager
import android.os.Looper
import androidx.core.content.ContextCompat
import com.google.android.gms.location.LocationCallback
import com.google.android.gms.location.LocationRequest
import com.google.android.gms.location.LocationResult
import com.google.android.gms.location.LocationServices
import com.google.android.gms.location.Priority
import com.jarvis.android.domain.model.LocationReading
import com.jarvis.android.domain.model.OrientationReading
import com.jarvis.android.domain.model.SensorInfo
import com.jarvis.android.domain.model.SensorReading
import com.jarvis.android.domain.repository.SensorRepository
import dagger.hilt.android.qualifiers.ApplicationContext
import kotlinx.coroutines.channels.awaitClose
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.callbackFlow
import kotlinx.coroutines.flow.conflate
import javax.inject.Inject
import javax.inject.Singleton

@Singleton
class SensorRepositoryImpl @Inject constructor(
    @ApplicationContext private val context: Context,
) : SensorRepository {

    private val sensorManager: SensorManager =
        context.getSystemService(Context.SENSOR_SERVICE) as SensorManager

    // ── Available sensors ─────────────────────────────────────────────────

    override fun getAvailableSensors(): List<SensorInfo> =
        sensorManager.getSensorList(Sensor.TYPE_ALL).map { s ->
            SensorInfo(
                type       = s.type,
                name       = s.name,
                vendor     = s.vendor,
                version    = s.version,
                maxRange   = s.maximumRange,
                resolution = s.resolution,
                power      = s.power,
                minDelayUs = s.minDelay,
            )
        }

    // ── Sensor stream ─────────────────────────────────────────────────────

    /**
     * Registers a [SensorEventListener] for [type] and emits each reading.
     * The listener is unregistered automatically when the collecting coroutine
     * is cancelled (via [awaitClose]).
     *
     * [samplingUs] maps to [SensorManager.registerListener] delay — default
     * 200 ms (5 Hz) to avoid draining the battery.
     */
    override fun observeSensor(type: Int, samplingUs: Int): Flow<SensorReading> =
        callbackFlow {
            val sensor = sensorManager.getDefaultSensor(type)
                ?: run { close(); return@callbackFlow }

            val listener = object : SensorEventListener {
                override fun onSensorChanged(event: SensorEvent) {
                    trySend(
                        SensorReading(
                            sensorType  = event.sensor.type,
                            sensorName  = event.sensor.name,
                            values      = event.values.copyOf(),
                            accuracy    = event.accuracy,
                            timestampNs = event.timestamp,
                        )
                    )
                }
                override fun onAccuracyChanged(s: Sensor, accuracy: Int) = Unit
            }

            sensorManager.registerListener(listener, sensor, samplingUs)
            awaitClose { sensorManager.unregisterListener(listener) }
        }.conflate()

    // ── Location ──────────────────────────────────────────────────────────

    /**
     * Emits [LocationReading]s via the Fused Location Provider.
     * Falls back gracefully when location permission is not granted.
     */
    override fun observeLocation(): Flow<LocationReading> = callbackFlow {
        val hasPermission = ContextCompat.checkSelfPermission(
            context, Manifest.permission.ACCESS_FINE_LOCATION
        ) == PackageManager.PERMISSION_GRANTED

        if (!hasPermission) { close(); return@callbackFlow }

        val client  = LocationServices.getFusedLocationProviderClient(context)
        val request = LocationRequest.Builder(Priority.PRIORITY_HIGH_ACCURACY, 5_000L)
            .setMinUpdateIntervalMillis(2_000L)
            .build()

        val callback = object : LocationCallback() {
            override fun onLocationResult(result: LocationResult) {
                val loc = result.lastLocation ?: return
                trySend(
                    LocationReading(
                        latitudeDeg  = loc.latitude,
                        longitudeDeg = loc.longitude,
                        altitudeM    = loc.altitude,
                        accuracyM    = loc.accuracy,
                        speedMps     = loc.speed,
                        bearingDeg   = loc.bearing,
                        timestampMs  = loc.time,
                        provider     = loc.provider ?: "fused",
                    )
                )
            }
        }

        client.requestLocationUpdates(request, callback, Looper.getMainLooper())
        awaitClose { client.removeLocationUpdates(callback) }
    }.conflate()

    // ── Orientation ───────────────────────────────────────────────────────

    /**
     * Fuses accelerometer + magnetometer into device orientation angles.
     * Emits whenever either raw sensor fires.
     */
    override fun observeOrientation(): Flow<OrientationReading> = callbackFlow {
        val accel  = sensorManager.getDefaultSensor(Sensor.TYPE_ACCELEROMETER)
        val mag    = sensorManager.getDefaultSensor(Sensor.TYPE_MAGNETIC_FIELD)
        if (accel == null || mag == null) { close(); return@callbackFlow }

        val gravity   = FloatArray(3)
        val geoMag    = FloatArray(3)
        val rotMatrix = FloatArray(9)
        val orientation = FloatArray(3)

        val listener = object : SensorEventListener {
            override fun onSensorChanged(event: SensorEvent) {
                when (event.sensor.type) {
                    Sensor.TYPE_ACCELEROMETER  -> event.values.copyInto(gravity)
                    Sensor.TYPE_MAGNETIC_FIELD -> event.values.copyInto(geoMag)
                }
                if (SensorManager.getRotationMatrix(rotMatrix, null, gravity, geoMag)) {
                    SensorManager.getOrientation(rotMatrix, orientation)
                    trySend(
                        OrientationReading(
                            azimuthDeg = Math.toDegrees(orientation[0].toDouble()).toFloat(),
                            pitchDeg   = Math.toDegrees(orientation[1].toDouble()).toFloat(),
                            rollDeg    = Math.toDegrees(orientation[2].toDouble()).toFloat(),
                        )
                    )
                }
            }
            override fun onAccuracyChanged(s: Sensor, accuracy: Int) = Unit
        }

        sensorManager.registerListener(listener, accel, SensorManager.SENSOR_DELAY_UI)
        sensorManager.registerListener(listener, mag,   SensorManager.SENSOR_DELAY_UI)
        awaitClose { sensorManager.unregisterListener(listener) }
    }.conflate()
}
