package io.sahay.engine

import android.app.NotificationManager
import android.content.Context
import android.content.Intent
import androidx.core.app.NotificationCompat
import io.sahay.App
import io.sahay.R
import io.sahay.service.SahayForegroundService

/**
 * Global emergency stop.
 *
 * Triggered by:
 *   • Volume-down 3x in 2 seconds (handled in SahayAccessibilityService)
 *   • In-app "Stop" button
 *
 * Effect:
 *   • Stops the foreground service (kills any in-flight execution loop)
 *   • Disables auto-trigger until user re-enables in Settings
 *   • Posts a "Sahay paused" notification with one-tap resume
 */
object KillSwitch {

    @Volatile private var armed: Boolean = true

    val isArmed: Boolean get() = armed

    fun engage(context: Context) {
        armed = false
        val intent = Intent(context, SahayForegroundService::class.java)
        context.stopService(intent)
        notifyPaused(context)
    }

    fun rearm() {
        armed = true
    }

    private fun notifyPaused(context: Context) {
        val nm = context.getSystemService(NotificationManager::class.java) ?: return
        val notif = NotificationCompat.Builder(context, App.CHANNEL_ALERTS)
            .setSmallIcon(R.mipmap.ic_launcher)
            .setContentTitle("Sahay paused")
            .setContentText("Open the app to resume.")
            .setPriority(NotificationCompat.PRIORITY_DEFAULT)
            .setAutoCancel(true)
            .build()
        nm.notify(NOTIF_ID, notif)
    }

    private const val NOTIF_ID = 4242
}
