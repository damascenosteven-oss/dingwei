package com.familywatch.elderlocation.recovery

import android.Manifest
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import androidx.core.app.ActivityCompat
import androidx.core.content.ContextCompat
import androidx.work.CoroutineWorker
import androidx.work.ExistingPeriodicWorkPolicy
import androidx.work.PeriodicWorkRequestBuilder
import androidx.work.WorkManager
import androidx.work.WorkerParameters
import com.familywatch.elderlocation.ElderApplication
import com.familywatch.elderlocation.location.LocationService
import kotlinx.coroutines.flow.first
import java.util.concurrent.TimeUnit

class RecoveryWorker(context: Context, params: WorkerParameters) : CoroutineWorker(context, params) {
    override suspend fun doWork(): Result {
        val app = applicationContext as ElderApplication
        val config = app.container.config.config.first()
        if (!config.enrolled) {
            return runCatching { app.container.locations.sendPendingStop(); Result.success() }
                .getOrElse { Result.retry() }
        }
        if (ActivityCompat.checkSelfPermission(applicationContext, Manifest.permission.ACCESS_FINE_LOCATION) != PackageManager.PERMISSION_GRANTED) return Result.failure()
        runCatching {
            ContextCompat.startForegroundService(applicationContext, Intent(applicationContext, LocationService::class.java))
            app.container.locations.refreshConfig()
            app.container.locations.uploadPending()
        }.onFailure { return Result.retry() }
        return Result.success()
    }

    companion object {
        fun sendStop(context: Context) {
            val work = androidx.work.OneTimeWorkRequestBuilder<RecoveryWorker>().build()
            WorkManager.getInstance(context).enqueueUniqueWork(
                "elder-stop-sharing", androidx.work.ExistingWorkPolicy.KEEP, work,
            )
        }

        fun schedule(context: Context) {
            val work = PeriodicWorkRequestBuilder<RecoveryWorker>(15, TimeUnit.MINUTES)
                .build()
            WorkManager.getInstance(context).enqueueUniquePeriodicWork(
                "elder-location-recovery",
                ExistingPeriodicWorkPolicy.UPDATE,
                work,
            )
        }
    }
}
