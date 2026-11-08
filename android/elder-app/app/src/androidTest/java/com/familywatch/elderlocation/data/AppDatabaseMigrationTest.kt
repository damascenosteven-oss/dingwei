package com.familywatch.elderlocation.data

import android.content.Context
import androidx.sqlite.db.SupportSQLiteOpenHelper
import androidx.sqlite.db.framework.FrameworkSQLiteOpenHelperFactory
import androidx.test.core.app.ApplicationProvider
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

class AppDatabaseMigrationTest {
    private var helper: SupportSQLiteOpenHelper? = null

    @After
    fun closeDatabase() {
        helper?.close()
    }

    @Test
    fun migrationOneToTwoPreservesQueuedLocations() {
        val context = ApplicationProvider.getApplicationContext<Context>()
        context.deleteDatabase("migration-one-two.db")
        val configuration = SupportSQLiteOpenHelper.Configuration.builder(context)
            .name("migration-one-two.db")
            .callback(object : SupportSQLiteOpenHelper.Callback(1) {
                override fun onCreate(db: androidx.sqlite.db.SupportSQLiteDatabase) {
                    db.execSQL(
                        """CREATE TABLE pending_locations (
                            sequence INTEGER PRIMARY KEY AUTOINCREMENT NOT NULL,
                            capturedAtEpochMs INTEGER NOT NULL,
                            latitude REAL NOT NULL,
                            longitude REAL NOT NULL,
                            accuracyM REAL NOT NULL,
                            speedMps REAL,
                            batteryPct INTEGER NOT NULL,
                            isCharging INTEGER NOT NULL,
                            isMock INTEGER NOT NULL
                        )""".trimIndent()
                    )
                }

                override fun onUpgrade(
                    db: androidx.sqlite.db.SupportSQLiteDatabase,
                    oldVersion: Int,
                    newVersion: Int,
                ) = Unit
            })
            .build()
        helper = FrameworkSQLiteOpenHelperFactory().create(configuration)
        val database = helper!!.writableDatabase
        database.execSQL(
            "INSERT INTO pending_locations " +
                "(capturedAtEpochMs, latitude, longitude, accuracyM, speedMps, batteryPct, isCharging, isMock) " +
                "VALUES (1, 30.1, 120.2, 8.0, NULL, 80, 0, 0)"
        )

        AppDatabase.MIGRATION_1_2.migrate(database)

        database.query("SELECT sequence, locateRequestId FROM pending_locations").use { cursor ->
            assertEquals(true, cursor.moveToFirst())
            assertEquals(1, cursor.getLong(0))
            assertNull(cursor.getString(1))
        }
    }
}
