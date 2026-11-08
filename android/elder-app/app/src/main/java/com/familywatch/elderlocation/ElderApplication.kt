package com.familywatch.elderlocation

import android.app.ActivityManager
import android.app.Application
import android.app.ApplicationExitInfo
import android.os.Build
import com.familywatch.elderlocation.data.AppContainer
import com.familywatch.elderlocation.recovery.RecoveryWorker
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.launch
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.runBlocking

internal fun stopInvalidatesConsent(stopped: Boolean, exitAt: Long?, agreedAt: Long): Boolean =
    stopped && (exitAt == null || exitAt > agreedAt)

class ElderApplication : Application() {
    val container by lazy { AppContainer(this) }

    private val applicationScope = CoroutineScope(SupervisorJob() + Dispatchers.IO)

    fun synchronizeStop() {
        RecoveryWorker.sendStop(this)
        applicationScope.launch {
            try {
                container.locations.sendPendingStop()
            } catch (error: java.io.IOException) {
                container.config.recordServiceError("stop_pending")
            } catch (error: com.familywatch.elderlocation.data.ApiHttpException) {
                container.config.recordServiceError("stop_pending_http_" + error.statusCode)
            }
        }
    }

    override fun onCreate() {
        super.onCreate()
        val manager = getSystemService(ActivityManager::class.java)
        var exitAt: Long? = null
        val stopped = if (Build.VERSION.SDK_INT >= 30) {
            val exit = manager.getHistoricalProcessExitReasons(packageName, 0, 8)
                .firstOrNull { it.processName == packageName }
            exitAt = exit?.timestamp
            when {
                exit != null -> exit.reason == ApplicationExitInfo.REASON_USER_REQUESTED ||
                    exit.reason == ApplicationExitInfo.REASON_USER_STOPPED
                Build.VERSION.SDK_INT >= 35 ->
                    manager.getHistoricalProcessStartReasons(1).firstOrNull()?.wasForceStopped() == true
                else -> true
            }
        } else {
            // Android 10 cannot distinguish force-stop from other cold launches.
            true
        }

        // Apply revocation before any Activity, boot receiver or foreground service can restart collection.
        val revoked = runBlocking(Dispatchers.IO) {
            val config = container.config.config.first()
            if (!config.enrolled || !stopInvalidatesConsent(stopped, exitAt, config.consentAt)) {
                !config.enrolled && config.serverUrl.isNotBlank()
            } else {
                container.locations.clear()
                true
            }
        }
        if (revoked) synchronizeStop()
    }
}
