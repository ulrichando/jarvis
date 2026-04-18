package com.jarvis.android.domain.usecase

import com.jarvis.android.domain.model.LocationReading
import com.jarvis.android.domain.model.OrientationReading
import com.jarvis.android.domain.model.SensorInfo
import com.jarvis.android.domain.model.SensorReading
import com.jarvis.android.domain.repository.SensorRepository
import kotlinx.coroutines.flow.Flow
import javax.inject.Inject

class GetAvailableSensorsUseCase @Inject constructor(private val repo: SensorRepository) {
    operator fun invoke(): List<SensorInfo> = repo.getAvailableSensors()
}

class ObserveSensorUseCase @Inject constructor(private val repo: SensorRepository) {
    operator fun invoke(type: Int, samplingUs: Int = 200_000): Flow<SensorReading> =
        repo.observeSensor(type, samplingUs)
}

class ObserveLocationUseCase @Inject constructor(private val repo: SensorRepository) {
    operator fun invoke(): Flow<LocationReading> = repo.observeLocation()
}

class ObserveOrientationUseCase @Inject constructor(private val repo: SensorRepository) {
    operator fun invoke(): Flow<OrientationReading> = repo.observeOrientation()
}
