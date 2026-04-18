package com.jarvis.android.presentation.sensors

import android.hardware.Sensor
import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.jarvis.android.domain.model.LocationReading
import com.jarvis.android.domain.model.OrientationReading
import com.jarvis.android.domain.model.SensorInfo
import com.jarvis.android.domain.model.SensorReading
import com.jarvis.android.domain.usecase.GetAvailableSensorsUseCase
import com.jarvis.android.domain.usecase.ObserveLocationUseCase
import com.jarvis.android.domain.usecase.ObserveOrientationUseCase
import com.jarvis.android.domain.usecase.ObserveSensorUseCase
import dagger.hilt.android.lifecycle.HiltViewModel
import kotlinx.coroutines.Job
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.catch
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import javax.inject.Inject

enum class SensorTab { SENSORS, LOCATION, ORIENTATION }

data class SensorUiState(
    val activeTab:    SensorTab                     = SensorTab.SENSORS,
    val availableSensors: List<SensorInfo>          = emptyList(),
    val readings:     Map<Int, SensorReading>       = emptyMap(),  // keyed by Sensor.TYPE_*
    val location:     LocationReading?              = null,
    val orientation:  OrientationReading?           = null,
    val error:        String?                       = null,
)

sealed class SensorIntent {
    data class SelectTab(val tab: SensorTab) : SensorIntent()
    object ClearError : SensorIntent()
}

/** Priority set of sensor types shown in the live grid. */
private val TRACKED_SENSOR_TYPES = intArrayOf(
    Sensor.TYPE_ACCELEROMETER,
    Sensor.TYPE_GYROSCOPE,
    Sensor.TYPE_MAGNETIC_FIELD,
    Sensor.TYPE_GRAVITY,
    Sensor.TYPE_LINEAR_ACCELERATION,
    Sensor.TYPE_ROTATION_VECTOR,
    Sensor.TYPE_LIGHT,
    Sensor.TYPE_PRESSURE,
    Sensor.TYPE_AMBIENT_TEMPERATURE,
    Sensor.TYPE_RELATIVE_HUMIDITY,
    Sensor.TYPE_PROXIMITY,
    Sensor.TYPE_STEP_COUNTER,
)

@HiltViewModel
class SensorViewModel @Inject constructor(
    private val getAvailableSensors:  GetAvailableSensorsUseCase,
    private val observeSensor:        ObserveSensorUseCase,
    private val observeLocation:      ObserveLocationUseCase,
    private val observeOrientation:   ObserveOrientationUseCase,
) : ViewModel() {

    private val _uiState = MutableStateFlow(SensorUiState())
    val uiState: StateFlow<SensorUiState> = _uiState.asStateFlow()

    private val sensorJobs = mutableMapOf<Int, Job>()
    private var locationJob: Job? = null
    private var orientationJob: Job? = null

    init {
        val available = getAvailableSensors()
        _uiState.update { it.copy(availableSensors = available) }
        startSensorObservers(available)
        startLocationObserver()
        startOrientationObserver()
    }

    fun onIntent(intent: SensorIntent) = when (intent) {
        is SensorIntent.SelectTab  -> _uiState.update { it.copy(activeTab = intent.tab) }
        is SensorIntent.ClearError -> _uiState.update { it.copy(error = null) }
    }

    private fun startSensorObservers(available: List<SensorInfo>) {
        val availableTypes = available.map { it.type }.toSet()
        TRACKED_SENSOR_TYPES.forEach { type ->
            if (type !in availableTypes) return@forEach
            sensorJobs[type] = viewModelScope.launch {
                observeSensor(type, samplingUs = 200_000)
                    .catch { e -> _uiState.update { it.copy(error = e.message) } }
                    .collect { reading ->
                        _uiState.update { s ->
                            s.copy(readings = s.readings + (type to reading))
                        }
                    }
            }
        }
    }

    private fun startLocationObserver() {
        locationJob = viewModelScope.launch {
            observeLocation()
                .catch { e -> _uiState.update { it.copy(error = e.message) } }
                .collect { loc -> _uiState.update { it.copy(location = loc) } }
        }
    }

    private fun startOrientationObserver() {
        orientationJob = viewModelScope.launch {
            observeOrientation()
                .catch { e -> _uiState.update { it.copy(error = e.message) } }
                .collect { ori -> _uiState.update { it.copy(orientation = ori) } }
        }
    }

    override fun onCleared() {
        sensorJobs.values.forEach { it.cancel() }
        locationJob?.cancel()
        orientationJob?.cancel()
    }
}
