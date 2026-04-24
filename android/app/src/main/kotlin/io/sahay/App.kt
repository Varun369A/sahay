package io.sahay

import android.app.Application
import android.app.NotificationChannel
import android.app.NotificationManager
import android.content.Context
import android.content.IntentFilter
import android.os.Build
import dagger.hilt.android.HiltAndroidApp
import io.sahay.BuildConfig
import io.sahay.service.DebugDumpReceiver

@HiltAndroidApp
class App : Application() {

    override fun onCreate() {
        super.onCreate()
        createNotificationChannels()
        if (BuildConfig.DEBUG) {
            // Allow `adb shell am broadcast -a io.sahay.debug.DUMP_TREE`
            // to dump the live accessibility tree to external files dir.
            val receiver = DebugDumpReceiver()
            val filter = IntentFilter(DebugDumpReceiver.ACTION_DUMP)
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
                registerReceiver(receiver, filter, Context.RECEIVER_EXPORTED)
            } else {
                @Suppress("UnspecifiedRegisterReceiverFlag")
                registerReceiver(receiver, filter)
            }
        }
    }

    private fun createNotificationChannels() {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.O) return
        val nm = getSystemService(NotificationManager::class.java) ?: return

        nm.createNotificationChannel(
            NotificationChannel(
                CHANNEL_FOREGROUND,
                "Sahay (running)",
                NotificationManager.IMPORTANCE_MIN,
            ).apply {
                description = "Persistent notification while Sahay is active."
                setShowBadge(false)
            }
        )

        nm.createNotificationChannel(
            NotificationChannel(
                CHANNEL_ALERTS,
                "Sahay alerts",
                NotificationManager.IMPORTANCE_DEFAULT,
            ).apply {
                description = "Action results, usage warnings, and confirmations."
            }
        )
    }

    companion object {
        const val CHANNEL_FOREGROUND = "sahay.foreground"
        const val CHANNEL_ALERTS = "sahay.alerts"
    }
}
