package com.familywatch.elderlocation.location

class SamplingPolicy(
    private val movingIntervalMs: Long = 30_000,
    private val stationaryIntervalMs: Long = 180_000,
) {
    private var slowSamples = 0
    var currentIntervalMs: Long = movingIntervalMs
        private set

    fun onSpeed(speedMps: Float): Long {
        if (speedMps >= 0.5f) {
            slowSamples = 0
            currentIntervalMs = movingIntervalMs
        } else {
            slowSamples += 1
            if (slowSamples >= 3) currentIntervalMs = stationaryIntervalMs
        }
        return currentIntervalMs
    }
}
