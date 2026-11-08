package com.familywatch.elderlocation.location

import org.junit.Assert.assertEquals
import org.junit.Test

class CommandRetryPolicyTest {
    @Test
    fun backsOffAndCapsAtThirtySeconds() {
        val policy = CommandRetryPolicy()
        assertEquals(2_000, policy.nextDelayMs())
        assertEquals(5_000, policy.nextDelayMs())
        assertEquals(15_000, policy.nextDelayMs())
        assertEquals(30_000, policy.nextDelayMs())
        assertEquals(30_000, policy.nextDelayMs())
    }

    @Test
    fun successfulLongPollResetsBackoff() {
        val policy = CommandRetryPolicy()
        policy.nextDelayMs()
        policy.nextDelayMs()
        policy.reset()
        assertEquals(2_000, policy.nextDelayMs())
    }
}
