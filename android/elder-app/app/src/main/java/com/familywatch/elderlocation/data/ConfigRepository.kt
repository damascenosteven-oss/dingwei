package com.familywatch.elderlocation.data

import android.content.Context
import androidx.datastore.preferences.core.booleanPreferencesKey
import androidx.datastore.preferences.core.doublePreferencesKey
import androidx.datastore.preferences.core.edit
import androidx.datastore.preferences.core.intPreferencesKey
import androidx.datastore.preferences.core.longPreferencesKey
import androidx.datastore.preferences.core.stringPreferencesKey
import androidx.datastore.preferences.preferencesDataStore
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.flow.map
import java.security.SecureRandom
import java.util.Base64
import javax.crypto.SecretKeyFactory
import javax.crypto.spec.PBEKeySpec

private val Context.dataStore by preferencesDataStore("elder_config")

data class AppConfig(
    val serverUrl: String = "",
    val elderDisplayName: String = "",
    val timezone: String = "Asia/Shanghai",
    val enrolled: Boolean = false,
    val privacyAccepted: Boolean = false,
    val consentAt: Long = 0,
    val lastCaptureAt: Long = 0,
    val lastReject: String = "",
    val lastUploadAt: Long = 0,
    val lastBatteryPct: Int = 0,
    val configVersion: Int = 1,
    val homeLatitude: Double? = null,
    val homeLongitude: Double? = null,
    val homeRadiusM: Int? = null,
    val homeUpdatedAt: String = "",
    val lastConfigSyncAt: Long = 0,
    val lastServiceStartedAt: Long = 0,
    val lastCommandContactAt: Long = 0,
    val lastServiceError: String = "",
)

class ConfigRepository(private val context: Context) {
    private object Keys {
        val serverUrl = stringPreferencesKey("server_url")
        val elderName = stringPreferencesKey("elder_name")
        val timezone = stringPreferencesKey("timezone")
        val enrolled = booleanPreferencesKey("enrolled")
        val consentAt = longPreferencesKey("consent_at")
        val privacy = booleanPreferencesKey("privacy_accepted")
        val lastCapture = longPreferencesKey("last_capture")
        val lastReject = stringPreferencesKey("last_reject")
        val lastUpload = longPreferencesKey("last_upload_at")
        val lastBattery = intPreferencesKey("last_battery")
        val configVersion = intPreferencesKey("config_version")
        val homeLatitude = doublePreferencesKey("home_latitude")
        val homeLongitude = doublePreferencesKey("home_longitude")
        val homeRadius = intPreferencesKey("home_radius")
        val homeUpdatedAt = stringPreferencesKey("home_updated_at")
        val lastConfigSync = longPreferencesKey("last_config_sync_at")
        val lastServiceStarted = longPreferencesKey("last_service_started_at")
        val lastCommandContact = longPreferencesKey("last_command_contact_at")
        val lastServiceError = stringPreferencesKey("last_service_error")
        val pinSalt = stringPreferencesKey("pin_salt")
        val pinHash = stringPreferencesKey("pin_hash")
    }

    val config: Flow<AppConfig> = context.dataStore.data.map { preferences ->
        AppConfig(
            serverUrl = preferences[Keys.serverUrl].orEmpty(),
            elderDisplayName = preferences[Keys.elderName].orEmpty(),
            timezone = preferences[Keys.timezone] ?: "Asia/Shanghai",
            enrolled = preferences[Keys.enrolled] ?: false,
            privacyAccepted = preferences[Keys.privacy] ?: false,
            consentAt = preferences[Keys.consentAt] ?: 0,
            lastCaptureAt = preferences[Keys.lastCapture] ?: 0,
            lastReject = preferences[Keys.lastReject].orEmpty(),
            lastUploadAt = preferences[Keys.lastUpload] ?: 0,
            lastBatteryPct = preferences[Keys.lastBattery] ?: 0,
            configVersion = preferences[Keys.configVersion] ?: 1,
            homeLatitude = preferences[Keys.homeLatitude],
            homeLongitude = preferences[Keys.homeLongitude],
            homeRadiusM = preferences[Keys.homeRadius],
            homeUpdatedAt = preferences[Keys.homeUpdatedAt].orEmpty(),
            lastConfigSyncAt = preferences[Keys.lastConfigSync] ?: 0,
            lastServiceStartedAt = preferences[Keys.lastServiceStarted] ?: 0,
            lastCommandContactAt = preferences[Keys.lastCommandContact] ?: 0,
            lastServiceError = preferences[Keys.lastServiceError].orEmpty(),
        )
    }

    suspend fun saveEnrollment(serverUrl: String, elderName: String, timezone: String, pin: String) {
        val salt = ByteArray(16).also(SecureRandom()::nextBytes)
        context.dataStore.edit {
            it[Keys.serverUrl] = serverUrl.trimEnd('/')
            it[Keys.elderName] = elderName
            it[Keys.timezone] = timezone
            it[Keys.enrolled] = true
            it[Keys.privacy] = true
            it[Keys.consentAt] = System.currentTimeMillis()
            it[Keys.pinSalt] = Base64.getEncoder().encodeToString(salt)
            it[Keys.pinHash] = hashPin(pin, salt)
            it.remove(Keys.homeLatitude)
            it.remove(Keys.homeLongitude)
            it.remove(Keys.homeRadius)
            it.remove(Keys.homeUpdatedAt)
            it.remove(Keys.lastConfigSync)
        }
    }

    suspend fun verifyPin(pin: String): Boolean {
        val preferences = context.dataStore.data.first()
        val salt = preferences[Keys.pinSalt]?.let(Base64.getDecoder()::decode) ?: return false
        return preferences[Keys.pinHash] == hashPin(pin, salt)
    }

    suspend fun disableSharing() {
        context.dataStore.edit { it[Keys.enrolled] = false; it[Keys.privacy] = false }
    }

    suspend fun captured(time: Long) {
        context.dataStore.edit { it[Keys.lastCapture] = maxOf(time, it[Keys.lastCapture] ?: 0) }
    }

    suspend fun rejected(reason: String) {
        context.dataStore.edit { it[Keys.lastReject] = reason.take(120) }
    }

    suspend fun updateStatus(uploadAt: Long, batteryPct: Int) {
        context.dataStore.edit {
            it[Keys.lastUpload] = uploadAt
            it[Keys.lastBattery] = batteryPct
            if (it[Keys.lastServiceError] == "network_error") it.remove(Keys.lastServiceError)
        }
    }

    suspend fun markServiceStarted() {
        context.dataStore.edit {
            it[Keys.lastServiceStarted] = System.currentTimeMillis()
            it.remove(Keys.lastServiceError)
        }
    }

    suspend fun markCommandContact() {
        context.dataStore.edit {
            it[Keys.lastCommandContact] = System.currentTimeMillis()
            if (it[Keys.lastServiceError] == "command_channel_error") it.remove(Keys.lastServiceError)
        }
    }

    suspend fun recordServiceError(code: String) {
        context.dataStore.edit { it[Keys.lastServiceError] = code.take(120) }
    }

    suspend fun updateRemoteConfig(remote: DeviceConfigResponse) {
        context.dataStore.edit {
            it[Keys.elderName] = remote.elderDisplayName
            it[Keys.timezone] = remote.timezone
            it[Keys.configVersion] = remote.configVersion
            it[Keys.lastConfigSync] = System.currentTimeMillis()
            val home = remote.homeGeofence
            if (home == null) {
                it.remove(Keys.homeLatitude)
                it.remove(Keys.homeLongitude)
                it.remove(Keys.homeRadius)
                it.remove(Keys.homeUpdatedAt)
            } else {
                it[Keys.homeLatitude] = home.latitude
                it[Keys.homeLongitude] = home.longitude
                it[Keys.homeRadius] = home.radiusM
                it[Keys.homeUpdatedAt] = home.updatedAt
            }
        }
    }

    suspend fun clearEnrollment() {
        context.dataStore.edit { preferences -> preferences.clear() }
    }

    private fun hashPin(pin: String, salt: ByteArray): String {
        val spec = PBEKeySpec(pin.toCharArray(), salt, 120_000, 256)
        val hash = SecretKeyFactory.getInstance("PBKDF2WithHmacSHA256").generateSecret(spec).encoded
        return Base64.getEncoder().encodeToString(hash)
    }
}
