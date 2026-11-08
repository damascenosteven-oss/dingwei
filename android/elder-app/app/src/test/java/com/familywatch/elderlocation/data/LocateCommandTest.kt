package com.familywatch.elderlocation.data

import kotlinx.serialization.json.Json
import org.junit.Assert.assertEquals
import org.junit.Test

class LocateCommandTest {
    @Test
    fun parsesLocateNowCommand() {
        val command = Json.decodeFromString<LocateCommand>(
            """{"id":"request-1","type":"locate_now","expiresAt":"2026-10-13T11:00:00Z"}"""
        )
        assertEquals("request-1", command.id)
        assertEquals("locate_now", command.type)
        assertEquals("2026-10-13T11:00:00Z", command.expiresAt)
    }
}
