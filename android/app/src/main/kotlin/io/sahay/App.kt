package io.sahay

import android.app.Application
import android.app.NotificationChannel
import android.app.NotificationManager
import android.os.Build
import dagger.hilt.android.HiltAndroidApp

@HiltAndroidApp
class App : Application() {

    override fun onCreate() {
        super.onCreate()
        createNotificationChannels()
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
