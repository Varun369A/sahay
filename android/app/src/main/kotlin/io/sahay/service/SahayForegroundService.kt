package io.sahay.service

import android.app.Notification
import android.app.PendingIntent
import android.app.Service
import android.content.Intent
import android.content.pm.ServiceInfo
import android.os.Build
import android.os.IBinder
import androidx.core.app.NotificationCompat
import dagger.hilt.android.AndroidEntryPoint
import io.sahay.App
import io.sahay.MainActivity
import io.sahay.R
import io.sahay.engine.ActionRunner
import javax.inject.Inject
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.launch

/**
 * Persistent foreground service that:
 *   • Shows the always-visible "Sahay running" notification
 *   • Owns the ActionRunner — coordinates planning + execution + telemetry
 *   • Receives intents from the UI / mic to start a new action
 *
 * Stopping this service stops everything. KillSwitch triggers stopService.
 */
@AndroidEntryPoint
class SahayForegroundService : Service() {

    @Inject lateinit var runner: ActionRunner

    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Default)
    private var currentJob: Job? = null

    override fun onCreate() {
        super.onCreate()
        startForegroundCompat()
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        val command = intent?.getStringExtra(EXTRA_COMMAND)
        when (command) {
            CMD_RUN_INTENT -> {
                val appPackage = intent.getStringExtra(EXTRA_APP_PACKAGE) ?: return START_STICKY
                val sahayIntent = intent.getStringExtra(EXTRA_INTENT) ?: return START_STICKY
                val inputsJson = intent.getStringExtra(EXTRA_INPUTS_JSON) ?: "{}"
                currentJob?.cancel()
                currentJob = scope.launch {
                    runner.run(appPackage, sahayIntent, inputsJson)
                }
            }
            CMD_STOP -> stopSelf()
        }
        return START_STICKY
    }

    override fun onDestroy() {
        scope.cancel()
        super.onDestroy()
    }

    override fun onBind(intent: Intent?): IBinder? = null

    private fun startForegroundCompat() {
        val pi = PendingIntent.getActivity(
            this, 0,
            Intent(this, MainActivity::class.java),
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT,
        )
        val notif: Notification = NotificationCompat.Builder(this, App.CHANNEL_FOREGROUND)
            .setSmallIcon(R.mipmap.ic_launcher)
            .setContentTitle("Sahay is running")
            .setContentText("Tap to open. Volume-down 3× to stop.")
            .setOngoing(true)
            .setShowWhen(false)
            .setPriority(NotificationCompat.PRIORITY_MIN)
            .setContentIntent(pi)
            .build()

        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.UPSIDE_DOWN_CAKE) {
            startForeground(NOTIF_ID, notif, ServiceInfo.FOREGROUND_SERVICE_TYPE_SPECIAL_USE)
        } else {
            startForeground(NOTIF_ID, notif)
        }
    }

    companion object {
        const val EXTRA_COMMAND = "cmd"
        const val EXTRA_APP_PACKAGE = "app_package"
        const val EXTRA_INTENT = "intent"
        const val EXTRA_INPUTS_JSON = "inputs_json"

        const val CMD_RUN_INTENT = "run_intent"
        const val CMD_STOP = "stop"

        private const val NOTIF_ID = 1001

        fun runIntent(context: android.content.Context, appPackage: String, intent: String, inputsJson: String) {
            val i = Intent(context, SahayForegroundService::class.java).apply {
                putExtra(EXTRA_COMMAND, CMD_RUN_INTENT)
                putExtra(EXTRA_APP_PACKAGE, appPackage)
                putExtra(EXTRA_INTENT, intent)
                putExtra(EXTRA_INPUTS_JSON, inputsJson)
            }
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
                context.startForegroundService(i)
            } else {
                context.startService(i)
            }
        }
    }
}
