package com.familywatch.elderlocation.location

import android.Manifest
import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Intent
import android.net.ConnectivityManager
import android.net.Network
import android.content.pm.PackageManager
import android.os.BatteryManager
import android.os.IBinder
import android.os.SystemClock
import androidx.core.app.ActivityCompat
import androidx.core.app.NotificationCompat
import androidx.core.app.ServiceCompat
import androidx.core.content.getSystemService
import com.amap.api.location.AMapLocation
import com.amap.api.location.AMapLocationClient
import com.amap.api.location.AMapLocationClientOption
import com.familywatch.elderlocation.ElderApplication
import com.familywatch.elderlocation.MainActivity
import com.familywatch.elderlocation.R
import com.familywatch.elderlocation.data.LocateCommand
import com.familywatch.elderlocation.data.PendingLocation
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import java.time.Instant

class LocationService : Service() {
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
    private lateinit var application: ElderApplication
    private var client: AMapLocationClient? = null
    private val sampling = SamplingPolicy()
    private val retryPolicy = CommandRetryPolicy()
    private var uploadJob: Job? = null
    private var commandJob: Job? = null
    private var locateTimeoutJob: Job? = null
    private var locateCompletionJob: Job? = null
    private var activeLocateRequestId: String? = null
    private var bestActiveLocation: AMapLocation? = null
    private var activeLocateSawMock = false
    private var activeLocateSawLowAccuracy = false
    private var lastConfigRefreshElapsedMs = 0L

    private val networkCallback = object : ConnectivityManager.NetworkCallback() {
        override fun onAvailable(network: Network) {
            scope.launch {
                runCatching { application.container.locations.uploadPending() }
                    .onFailure { application.container.config.recordServiceError("network_error") }
            }
        }
    }
    private var listeningForNetwork = false

    override fun onCreate() {
        super.onCreate()
        application = getApplication() as ElderApplication
        if (!hasLocationPermission()) {
            scope.launch {
                application.container.config.recordServiceError("permission_missing")
                stopSelf()
            }
            return
        }
        createChannel()
        ServiceCompat.startForeground(
            this,
            NOTIFICATION_ID,
            notification(),
            android.content.pm.ServiceInfo.FOREGROUND_SERVICE_TYPE_LOCATION,
        )
        scope.launch {
            val config = application.container.config.config.first()
            if (!config.enrolled || !config.privacyAccepted) {
                stopSelf()
                return@launch
            }
            application.container.config.markServiceStarted()
            startCommandLoop()
            withContext(Dispatchers.Main.immediate) {
                getSystemService<ConnectivityManager>()?.registerDefaultNetworkCallback(networkCallback)
                listeningForNetwork = true
                startLocationClient()
            }
            refreshRemoteConfig(force = true)
        }
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int = START_STICKY
    override fun onBind(intent: Intent?): IBinder? = null

    private fun startLocationClient() {
        if (!application.container.locations.locationServicesEnabled()) {
            scope.launch { application.container.config.recordServiceError("location_disabled") }
            return
        }
        AMapLocationClient.updatePrivacyShow(applicationContext, true, true)
        AMapLocationClient.updatePrivacyAgree(applicationContext, true)
        runCatching {
            AMapLocationClient(applicationContext).also { locationClient ->
                client = locationClient
                locationClient.setLocationListener(::onLocation)
                locationClient.setLocationOption(option(sampling.currentIntervalMs))
                locationClient.startLocation()
            }
        }.onFailure {
            scope.launch { application.container.config.recordServiceError("location_client_error") }
        }
    }

    private fun option(interval: Long, onDemand: Boolean = false) = AMapLocationClientOption().apply {
        locationMode = AMapLocationClientOption.AMapLocationMode.Hight_Accuracy
        isOnceLocation = false
        isOnceLocationLatest = false
        isNeedAddress = false
        isMockEnable = true
        isLocationCacheEnable = !onDemand
        httpTimeOut = ON_DEMAND_TIMEOUT_MS
        this.interval = interval
    }

    private fun onLocation(location: AMapLocation?) {
        if (location == null || location.errorCode != 0) {
            if (location?.errorCode != null) {
                scope.launch {
                    application.container.config.recordServiceError("location_error_${location.errorCode}")
                }
            }
            return
        }
        if (location.time <= 0 || location.coordType !in setOf("GCJ02", "WGS84")) {
            scope.launch { application.container.config.recordServiceError("invalid_location_metadata") }
            return
        }
        val requestId = activeLocateRequestId
        if (requestId != null) {
            when {
                location.isMock -> activeLocateSawMock = true
                location.accuracy > MAX_SAFE_ACCURACY_M -> activeLocateSawLowAccuracy = true
                bestActiveLocation == null || location.accuracy < bestActiveLocation!!.accuracy -> {
                    bestActiveLocation = location
                }
            }
            if (bestActiveLocation != null && locateCompletionJob?.isActive != true) {
                locateCompletionJob = scope.launch {
                    delay(ON_DEMAND_COLLECTION_MS)
                    withContext(Dispatchers.Main.immediate) { completeActiveLocate(requestId) }
                }
            }
            return
        }

        persistAndUpload(location, null)
        val previous = sampling.currentIntervalMs
        val next = sampling.onSpeed(location.speed.coerceAtLeast(0f))
        if (previous != next) client?.setLocationOption(option(next))
    }

    private fun persistAndUpload(location: AMapLocation, requestId: String?) {
        val battery = batteryState()
        scope.launch {
            application.container.locations.save(
                PendingLocation(
                    coordinateSystem = location.coordType,
                    capturedAtEpochMs = location.time,
                    latitude = location.latitude,
                    longitude = location.longitude,
                    accuracyM = location.accuracy,
                    speedMps = location.speed.takeIf { it >= 0 },
                    batteryPct = battery.first,
                    isCharging = battery.second,
                    isMock = location.isMock,
                    locateRequestId = requestId,
                )
            )
            if (requestId != null) {
                runCatching { application.container.locations.uploadPending() }
                    .onFailure {
                        application.container.config.recordServiceError("network_error")
                        runCatching { application.container.locations.failLocate(requestId, "network_error") }
                    }
                    .onSuccess { refreshRemoteConfig() }
            } else if (uploadJob?.isActive != true) {
                uploadJob = launch {
                    runCatching { application.container.locations.uploadPending() }
                        .onFailure { application.container.config.recordServiceError("network_error") }
                        .onSuccess { refreshRemoteConfig() }
                }
            }
        }
    }

    private fun startCommandLoop() {
        if (commandJob?.isActive == true) return
        commandJob = scope.launch {
            while (isActive) {
                try {
                    val error = application.container.config.config.first().lastServiceError
                    val command = application.container.locations.waitForCommand(error)
                    retryPolicy.reset()
                    if (command != null) handleCommand(command)
                } catch (cancelled: CancellationException) {
                    throw cancelled
                } catch (_: Exception) {
                    application.container.config.recordServiceError("command_channel_error")
                    delay(retryPolicy.nextDelayMs())
                }
            }
        }
    }

    private suspend fun handleCommand(command: LocateCommand) {
        if (command.type != "locate_now") return
        if (runCatching { Instant.parse(command.expiresAt).isBefore(Instant.now()) }.getOrDefault(false)) return
        application.container.locations.acknowledgeLocate(command.id)
        if (activeLocateRequestId == command.id) return

        val failure = when {
            !application.container.locations.fineLocationGranted()
                || !application.container.locations.backgroundLocationGranted() -> "permission_missing"
            !application.container.locations.locationServicesEnabled() -> "location_disabled"
            client == null -> "service_error"
            activeLocateRequestId != null -> "service_error"
            else -> null
        }
        if (failure != null) {
            application.container.locations.failLocate(command.id, failure)
            application.container.config.recordServiceError(failure)
            return
        }

        withContext(Dispatchers.Main.immediate) {
            activeLocateRequestId = command.id
            bestActiveLocation = null
            activeLocateSawMock = false
            activeLocateSawLowAccuracy = false
            client?.stopLocation()
            client?.setLocationOption(option(1_000L, onDemand = true))
            client?.startLocation()
        }
        locateTimeoutJob?.cancel()
        locateTimeoutJob = scope.launch {
            delay(ON_DEMAND_TIMEOUT_MS)
            if (activeLocateRequestId == command.id) {
                withContext(Dispatchers.Main.immediate) {
                    if (bestActiveLocation != null) {
                        completeActiveLocate(command.id)
                    } else {
                        val failureCode = when {
                            activeLocateSawMock -> "mock_location"
                            activeLocateSawLowAccuracy -> "low_accuracy"
                            else -> "location_timeout"
                        }
                        failActiveLocate(command.id, failureCode)
                        restoreContinuousLocation(command.id)
                    }
                }
            }
        }
    }

    private fun completeActiveLocate(requestId: String) {
        if (activeLocateRequestId != requestId) return
        val location = bestActiveLocation ?: return
        persistAndUpload(location, requestId)
        restoreContinuousLocation(requestId)
    }

    private fun failActiveLocate(requestId: String, failureCode: String) {
        scope.launch {
            runCatching { application.container.locations.failLocate(requestId, failureCode) }
            application.container.config.recordServiceError(failureCode)
        }
    }

    private fun restoreContinuousLocation(requestId: String) {
        if (activeLocateRequestId != requestId) return
        activeLocateRequestId = null
        bestActiveLocation = null
        activeLocateSawMock = false
        activeLocateSawLowAccuracy = false
        locateTimeoutJob?.cancel()
        locateTimeoutJob = null
        locateCompletionJob?.cancel()
        locateCompletionJob = null
        client?.stopLocation()
        client?.setLocationOption(option(sampling.currentIntervalMs))
        client?.startLocation()
    }

    private fun batteryState(): Pair<Int, Boolean> {
        val manager = getSystemService<BatteryManager>()
        val level = manager?.getIntProperty(BatteryManager.BATTERY_PROPERTY_CAPACITY)?.coerceIn(0, 100) ?: 0
        val status = manager?.getIntProperty(BatteryManager.BATTERY_PROPERTY_STATUS) ?: 0
        return level to (status == BatteryManager.BATTERY_STATUS_CHARGING || status == BatteryManager.BATTERY_STATUS_FULL)
    }

    private suspend fun refreshRemoteConfig(force: Boolean = false) {
        val elapsed = SystemClock.elapsedRealtime()
        if (!force && elapsed - lastConfigRefreshElapsedMs < CONFIG_REFRESH_INTERVAL_MS) return
        runCatching { application.container.locations.refreshConfig() }
            .onSuccess { lastConfigRefreshElapsedMs = elapsed }
    }

    private fun hasLocationPermission() = ActivityCompat.checkSelfPermission(
        this, Manifest.permission.ACCESS_FINE_LOCATION
    ) == PackageManager.PERMISSION_GRANTED

    private fun createChannel() {
        val channel = NotificationChannel(CHANNEL_ID, getString(R.string.location_channel), NotificationManager.IMPORTANCE_LOW).apply {
            description = "保证家人定位服务持续运行"
            setSound(null, null)
            enableVibration(false)
            setShowBadge(false)
            lockscreenVisibility = Notification.VISIBILITY_PRIVATE
        }
        getSystemService<NotificationManager>()?.createNotificationChannel(channel)
    }

    private fun notification(): Notification {
        val pendingIntent = PendingIntent.getActivity(
            this,
            0,
            Intent(this, MainActivity::class.java),
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT,
        )
        return NotificationCompat.Builder(this, CHANNEL_ID)
            .setSmallIcon(R.drawable.ic_location_notification)
            .setContentTitle(getString(R.string.location_running))
            .setContentText("正在后台守护，可接收家属的立即定位请求")
            .setContentIntent(pendingIntent)
            .setSilent(true)
            .setOngoing(true)
            .setOnlyAlertOnce(true)
            .setCategory(NotificationCompat.CATEGORY_SERVICE)
            .setPriority(NotificationCompat.PRIORITY_LOW)
            .build()
    }

    override fun onDestroy() {
        if (listeningForNetwork) getSystemService<ConnectivityManager>()?.unregisterNetworkCallback(networkCallback)
        client?.stopLocation()
        client?.onDestroy()
        client = null
        scope.cancel()
        super.onDestroy()
    }

    companion object {
        const val CHANNEL_ID = "elder_location_service"
        const val NOTIFICATION_ID = 4101
        private const val CONFIG_REFRESH_INTERVAL_MS = 15 * 60 * 1000L
        private const val ON_DEMAND_COLLECTION_MS = 3_000L
        private const val ON_DEMAND_TIMEOUT_MS = 20_000L
        private const val MAX_SAFE_ACCURACY_M = 200f
    }
}
