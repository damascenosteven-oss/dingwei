package com.familywatch.elderlocation.location

import org.junit.Assert.assertEquals
import org.junit.Test

class SamplingPolicyTest {
    @Test fun `uses three minutes only after three stationary samples`() {
        val policy = SamplingPolicy()
        assertEquals(30_000, policy.onSpeed(0.1f))
        assertEquals(30_000, policy.onSpeed(0.1f))
        assertEquals(180_000, policy.onSpeed(0.1f))
        assertEquals(30_000, policy.onSpeed(1.0f))
    }
}
