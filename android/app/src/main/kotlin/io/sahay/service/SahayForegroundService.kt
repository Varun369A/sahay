package io.sahay.service

import android.app.Notification
import android.app.PendingIntent
import android.app.Service
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.content.pm.ServiceInfo
import android.os.Build
import android.os.IBinder
import androidx.core.app.NotificationCompat
import androidx.core.content.ContextCompat
import dagger.hilt.android.AndroidEntryPoint
import io.sahay.App
import io.sahay.MainActivity
import io.sahay.R
import io.sahay.engine.ActionRunner
import io.sahay.engine.MorningBriefing
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
    @Inject lateinit var agentLoop: io.sahay.engine.AgentLoop
    @Inject lateinit var briefing: MorningBriefing

    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Default)
    private var currentJob: Job? = null

    /**
     * Fires on every screen-unlock. We forward to the MorningBriefing
     * gate which handles day-window / debounce / enable-flag checks.
     * Registered dynamically because Android 8+ blocks implicit broadcasts
     * in the manifest — but works fine from a running foreground service.
     */
    private val unlockReceiver = object : BroadcastReceiver() {
        override fun onReceive(context: Context?, intent: Intent?) {
            if (intent?.action != Intent.ACTION_USER_PRESENT) return
            scope.launch { briefing.maybeAnnounce() }
        }
    }

    override fun onCreate() {
        super.onCreate()
        startForegroundCompat()
        ContextCompat.registerReceiver(
            this,
            unlockReceiver,
            IntentFilter(Intent.ACTION_USER_PRESENT),
            ContextCompat.RECEIVER_NOT_EXPORTED,
        )
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
            CMD_RUN_AGENT -> {
                // Full-auto mode: Claude drives the phone screen-by-screen.
                val goal = intent.getStringExtra(EXTRA_GOAL) ?: return START_STICKY
                currentJob?.cancel()
                currentJob = scope.launch {
                    val outcome = agentLoop.run(goal)
                    android.util.Log.i("SahayForegroundSvc",
                        "agent_done success=${outcome.success} steps=${outcome.stepsUsed} " +
                        "reason=${outcome.reason} summary=${outcome.summary}")
                }
            }
            CMD_RUN_RESUME -> {
                // Conversation continuity: user replied to a previous
                // ask_user. Re-enter the SAME conversation with their
                // answer instead of starting a fresh task.
                val convId = intent.getStringExtra(EXTRA_CONVERSATION_ID) ?: return START_STICKY
                val originalGoal = intent.getStringExtra(EXTRA_GOAL) ?: return START_STICKY
                val reply = intent.getStringExtra(EXTRA_USER_REPLY) ?: return START_STICKY
                currentJob?.cancel()
                currentJob = scope.launch {
                    val outcome = agentLoop.resume(convId, originalGoal, reply)
                    android.util.Log.i("SahayForegroundSvc",
                        "agent_resume_done success=${outcome.success} steps=${outcome.stepsUsed} " +
                        "reason=${outcome.reason} summary=${outcome.summary}")
                }
            }
            CMD_STOP -> stopSelf()
        }
        return START_STICKY
    }

    override fun onDestroy() {
        runCatching { unregisterReceiver(unlockReceiver) }
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
        const val EXTRA_GOAL = "goal"
        const val EXTRA_CONVERSATION_ID = "conversation_id"
        const val EXTRA_USER_REPLY = "user_reply"

        const val CMD_RUN_INTENT = "run_intent"
        const val CMD_RUN_AGENT = "run_agent"
        const val CMD_RUN_RESUME = "run_resume"
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

        /** Full-auto mode — Claude agent drives the phone. */
        fun runAgent(context: android.content.Context, goal: String) {
            val i = Intent(context, SahayForegroundService::class.java).apply {
                putExtra(EXTRA_COMMAND, CMD_RUN_AGENT)
                putExtra(EXTRA_GOAL, goal)
            }
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
                context.startForegroundService(i)
            } else {
                context.startService(i)
            }
        }

        /**
         * Resume a paused conversation with the user's spoken reply to
         * the agent's previous ask_user. TaskWorker calls this when it
         * sees a fresh PendingClarification within the resume window.
         */
        fun runResume(
            context: android.content.Context,
            conversationId: String,
            originalGoal: String,
            userReply: String,
        ) {
            val i = Intent(context, SahayForegroundService::class.java).apply {
                putExtra(EXTRA_COMMAND, CMD_RUN_RESUME)
                putExtra(EXTRA_CONVERSATION_ID, conversationId)
                putExtra(EXTRA_GOAL, originalGoal)
                putExtra(EXTRA_USER_REPLY, userReply)
            }
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
                context.startForegroundService(i)
            } else {
                context.startService(i)
            }
        }
    }
}
