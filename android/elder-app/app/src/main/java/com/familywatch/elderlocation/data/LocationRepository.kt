package com.familywatch.elderlocation.data

import android.Manifest
import android.content.Context
import android.content.pm.PackageManager
import android.location.LocationManager
import android.net.ConnectivityManager
import android.net.NetworkCapabilities
import android.os.Build
import android.os.PowerManager
import android.provider.Settings
import androidx.core.content.ContextCompat
import androidx.core.content.getSystemService
import com.familywatch.elderlocation.BuildConfig
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import java.time.Instant

class LocationRepository(
    private val context: Context,
    private val database: AppDatabase,
    private val config: ConfigRepository,
    private val tokens: SecureTokenStore,
    private val api: ApiClient,
) {
    private val delivery = Mutex()
    private val collection = Mutex()

    suspend fun enroll(serverUrl: String, code: String, pin: String): EnrollResponse {
        if (networkLabel() == null) throw NoNetworkException()
        sendPendingStop()
        api.health(serverUrl)
        val installationId = Settings.Secure.getString(context.contentResolver, Settings.Secure.ANDROID_ID)
        val result = api.enroll(
            serverUrl,
            EnrollBody(code, installationId, "${Build.MANUFACTURER} ${Build.MODEL}", BuildConfig.VERSION_NAME),
        )
        tokens.save(result.deviceToken)
        config.saveEnrollment(serverUrl, result.elderDisplayName, result.timezone, pin)
        return result
    }

    suspend fun save(point: PendingLocation) = collection.withLock {
        if (config.config.first().enrolled) {
            database.locations().insert(point)
            config.captured(point.capturedAtEpochMs)
        }
    }

    suspend fun uploadPending(): Int = delivery.withLock {
        val current = config.config.first()
        if (!current.enrolled || !current.privacyAccepted) return@withLock 0
        val token = tokens.get() ?: return@withLock 0
        var count = 0
        while (config.config.first().enrolled) {
            val pending = database.locations().pending()
            if (pending.isEmpty()) break
            val response = api.upload(current.serverUrl, token, pending)
            val handled = response.accepted + response.duplicates + response.rejected.map { it.sequence }
            if (handled.toSet() != pending.map { it.sequence }.toSet()) {
                throw IllegalStateException("服务器未确认全部编号")
            }
            if (!config.config.first().enrolled) break
            if (response.accepted.isNotEmpty()) {
                val received = Instant.parse(requireNotNull(response.lastAcceptedAt)).toEpochMilli()
                val battery = pending.last { it.sequence in response.accepted }.batteryPct
                config.updateStatus(received, battery)
            }
            response.rejected.lastOrNull()?.let { config.rejected("#${it.sequence} ${it.reason}") }
            database.locations().delete(handled)
            count += response.accepted.size
        }
        count
    }

    suspend fun sendPendingStop() = delivery.withLock {
        val current = config.config.first()
        if (!current.enrolled && current.serverUrl.isNotBlank()) {
            database.locations().clear()
            tokens.get()?.let { api.stopSharing(current.serverUrl, it) }
            tokens.clear()
            config.clearEnrollment()
        }
    }

    suspend fun refreshConfig() {
        val current = config.config.first()
        val token = tokens.get() ?: return
        val remote = api.config(current.serverUrl, token)
        config.updateRemoteConfig(remote)
    }

    suspend fun waitForCommand(serviceError: String?): LocateCommand? {
        val current = config.config.first()
        val token = tokens.get() ?: return null
        val command = api.waitForCommand(
            current.serverUrl,
            token,
            DeviceCommandWaitBody(
                appVersion = BuildConfig.VERSION_NAME,
                backgroundLocationGranted = backgroundLocationGranted(),
                batteryOptimizationIgnored = batteryOptimizationIgnored(),
                locationServicesEnabled = locationServicesEnabled(),
                serviceError = serviceError?.ifBlank { null },
            ),
        )
        config.markCommandContact()
        return command
    }

    suspend fun acknowledgeLocate(requestId: String) {
        val current = config.config.first()
        val token = tokens.get() ?: return
        api.acknowledgeLocate(current.serverUrl, token, requestId)
    }

    suspend fun failLocate(requestId: String, failureCode: String) {
        val current = config.config.first()
        val token = tokens.get() ?: return
        api.failLocate(current.serverUrl, token, requestId, failureCode)
    }

    fun backgroundLocationGranted(): Boolean = ContextCompat.checkSelfPermission(
        context, Manifest.permission.ACCESS_BACKGROUND_LOCATION
    ) == PackageManager.PERMISSION_GRANTED

    fun fineLocationGranted(): Boolean = ContextCompat.checkSelfPermission(
        context, Manifest.permission.ACCESS_FINE_LOCATION
    ) == PackageManager.PERMISSION_GRANTED

    fun notificationGranted(): Boolean = Build.VERSION.SDK_INT < 33 || ContextCompat.checkSelfPermission(
        context, Manifest.permission.POST_NOTIFICATIONS
    ) == PackageManager.PERMISSION_GRANTED

    fun batteryOptimizationIgnored(): Boolean = context.getSystemService<PowerManager>()
        ?.isIgnoringBatteryOptimizations(context.packageName) == true

    fun locationServicesEnabled(): Boolean = context.getSystemService<LocationManager>()
        ?.isLocationEnabled == true

    fun networkLabel(): String? {
        val manager = context.getSystemService<ConnectivityManager>() ?: return null
        val network = manager.activeNetwork ?: return null
        val capabilities = manager.getNetworkCapabilities(network) ?: return null
        if (!capabilities.hasCapability(NetworkCapabilities.NET_CAPABILITY_INTERNET)) return null
        return when {
            capabilities.hasTransport(NetworkCapabilities.TRANSPORT_WIFI) -> "Wi‑Fi"
            capabilities.hasTransport(NetworkCapabilities.TRANSPORT_CELLULAR) -> "移动数据"
            capabilities.hasTransport(NetworkCapabilities.TRANSPORT_ETHERNET) -> "有线网络"
            else -> "其他网络"
        }
    }

    suspend fun pendingCount(): Int = database.locations().count()
    suspend fun verifyPin(pin: String) = config.verifyPin(pin)
    suspend fun clear() = collection.withLock {
        config.disableSharing()
        database.locations().clear()
    }
}
