package com.familywatch.elderlocation

import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class ConsentRestartTest {
    @Test fun explicitStopAfterAgreementRequiresNewConsent() {
        assertTrue(stopInvalidatesConsent(stopped = true, exitAt = 200, agreedAt = 100))
        assertTrue(stopInvalidatesConsent(stopped = true, exitAt = null, agreedAt = 100))
    }

    @Test fun memoryRecoveryAndOldStopPreserveNewAgreement() {
        assertFalse(stopInvalidatesConsent(stopped = false, exitAt = 200, agreedAt = 100))
        assertFalse(stopInvalidatesConsent(stopped = true, exitAt = 100, agreedAt = 200))
    }
}
