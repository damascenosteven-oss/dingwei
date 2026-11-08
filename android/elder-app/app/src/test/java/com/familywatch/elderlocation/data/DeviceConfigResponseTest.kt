package com.familywatch.elderlocation.data

import kotlinx.serialization.json.Json
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

class DeviceConfigResponseTest {
    private val json = Json { ignoreUnknownKeys = true }

    @Test fun `parses version two home geofence config`() {
        val response = json.decodeFromString<DeviceConfigResponse>(
            """{"elderDisplayName":"张奶奶","timezone":"Asia/Shanghai","configVersion":2,"homeGeofence":{"latitude":31.2,"longitude":121.4,"radiusM":5000,"updatedAt":"2026-09-29T10:00:00Z"}}"""
        )
        assertEquals(2, response.configVersion)
        assertEquals(5000, response.homeGeofence?.radiusM)
    }

    @Test fun `keeps older config response compatible`() {
        val response = json.decodeFromString<DeviceConfigResponse>(
            """{"elderDisplayName":"张奶奶","timezone":"Asia/Shanghai"}"""
        )
        assertEquals(1, response.configVersion)
        assertNull(response.homeGeofence)
    }
}
