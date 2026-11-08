package com.familywatch.elderlocation.data

import kotlinx.serialization.json.Json
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

class BatchResponseTest {
    @Test fun rejectionCarriesNoSuccessTime() {
        val ack = Json.decodeFromString<BatchResponse>(
            """{"acceptedSequences":[],"duplicateSequences":[],"rejected":[{"sequence":5,"reason":"invalid"}],"lastAcceptedAt":null}"""
        )
        assertNull(ack.lastAcceptedAt)
        assertEquals("invalid", ack.rejected.single().reason)
    }
}
