package com.familywatch.elderlocation.recovery

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import androidx.core.content.ContextCompat
import com.familywatch.elderlocation.location.LocationService

class BootReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        if (intent.action !in setOf(Intent.ACTION_BOOT_COMPLETED, Intent.ACTION_MY_PACKAGE_REPLACED)) return
        RecoveryWorker.schedule(context)
        runCatching { ContextCompat.startForegroundService(context, Intent(context, LocationService::class.java)) }
    }
}
