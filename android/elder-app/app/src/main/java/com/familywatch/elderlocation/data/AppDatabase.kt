package com.familywatch.elderlocation.data

import android.content.Context
import androidx.room.Dao
import androidx.room.Database
import androidx.room.Entity
import androidx.room.Insert
import androidx.room.PrimaryKey
import androidx.room.Query
import androidx.room.Room
import androidx.room.migration.Migration
import androidx.sqlite.db.SupportSQLiteDatabase

@Entity(tableName = "pending_locations")
data class PendingLocation(
    @PrimaryKey(autoGenerate = true) val sequence: Long = 0,
    val capturedAtEpochMs: Long,
    val latitude: Double,
    val longitude: Double,
    val accuracyM: Float,
    val speedMps: Float?,
    val batteryPct: Int,
    val isCharging: Boolean,
    val isMock: Boolean,
    val locateRequestId: String? = null,
    @androidx.room.ColumnInfo(defaultValue = "'GCJ02'") val coordinateSystem: String = "GCJ02",
)

@Dao
interface LocationDao {
    @Insert suspend fun insert(point: PendingLocation): Long
    @Query("SELECT * FROM pending_locations ORDER BY sequence LIMIT :limit")
    suspend fun pending(limit: Int = 500): List<PendingLocation>
    @Query("DELETE FROM pending_locations WHERE sequence IN (:sequences)")
    suspend fun delete(sequences: List<Long>)
    @Query("SELECT COUNT(*) FROM pending_locations")
    suspend fun count(): Int
    @Query("SELECT COUNT(*) FROM pending_locations")
    fun observeCount(): kotlinx.coroutines.flow.Flow<Int>
    @Query("DELETE FROM pending_locations")
    suspend fun clear()
}

@Database(entities = [PendingLocation::class], version = 3, exportSchema = true)
abstract class AppDatabase : androidx.room.RoomDatabase() {
    abstract fun locations(): LocationDao

    companion object {
        fun create(context: Context): AppDatabase = Room.databaseBuilder(
            context,
            AppDatabase::class.java,
            "elder-location.db",
        ).addMigrations(MIGRATION_1_2, MIGRATION_2_3).build()

        val MIGRATION_2_3 = object : Migration(2, 3) {
            override fun migrate(db: SupportSQLiteDatabase) {
                db.execSQL("ALTER TABLE pending_locations ADD COLUMN coordinateSystem TEXT NOT NULL DEFAULT 'GCJ02'")
            }
        }

        val MIGRATION_1_2 = object : Migration(1, 2) {
            override fun migrate(db: SupportSQLiteDatabase) {
                db.execSQL("ALTER TABLE pending_locations ADD COLUMN locateRequestId TEXT")
            }
        }
    }
}
