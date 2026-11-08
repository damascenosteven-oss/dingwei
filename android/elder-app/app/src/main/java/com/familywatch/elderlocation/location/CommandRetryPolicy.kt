package com.familywatch.elderlocation.location

class CommandRetryPolicy(
    private val delaysMs: LongArray = longArrayOf(2_000, 5_000, 15_000, 30_000),
) {
    private var failureCount = 0

    fun nextDelayMs(): Long {
        val value = delaysMs[failureCount.coerceAtMost(delaysMs.lastIndex)]
        failureCount += 1
        return value
    }

    fun reset() {
        failureCount = 0
    }
}
