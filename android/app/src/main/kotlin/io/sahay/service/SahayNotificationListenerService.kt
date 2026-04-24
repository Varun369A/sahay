package io.sahay.service

import android.app.Notification
import android.service.notification.NotificationListenerService
import android.service.notification.StatusBarNotification
import dagger.hilt.android.AndroidEntryPoint
import io.sahay.data.api.NotificationIn
import io.sahay.data.api.NotificationIngestRequest
import io.sahay.data.api.SahayApi
import io.sahay.data.secrets.UserPreferences
import io.sahay.engine.PaymentBlocklist
import java.util.concurrent.ConcurrentLinkedQueue
import javax.inject.Inject
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock

/**
 * Listens to system notifications and forwards a compact, privacy-scrubbed
 * subset to the Sahay backend so Claude can see "read my WhatsApp",
 * "did my boss message?", etc.
 *
 * Privacy rules (enforced here, also on backend):
 *   • Payment / banking / UPI / wallet apps are NEVER forwarded.
 *   • Our own notifications are dropped.
 *   • Group-summary + system-internal "ongoing" notifications (music, nav)
 *     are dropped (low signal, high noise).
 *   • If the user hasn't toggled notifications on in Settings, nothing is
 *     sent — the service runs (system binds it) but ingestion is a no-op.
 *
 * Delivery:
 *   We accumulate notifications in an in-memory queue and flush via a
 *   coroutine with a small debounce (1.5s). Android fires posts in bursts
 *   (screen unlock replays the stack); without debouncing we'd issue one
 *   HTTP call per notification — wasteful and noisy for the backend.
 */
@AndroidEntryPoint
class SahayNotificationListenerService : NotificationListenerService() {

    @Inject lateinit var api: SahayApi
    @Inject lateinit var prefs: UserPreferences

    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
    private val pending = ConcurrentLinkedQueue<NotificationIn>()
    private val flushMutex = Mutex()
    private var flushJob: Job? = null

    override fun onListenerConnected() {
        super.onListenerConnected()
        // Intentionally NOT replaying getActiveNotifications() — shipping
        // pre-grant history off-device is a consent violation.
    }

    override fun onListenerDisconnected() {
        super.onListenerDisconnected()
        pending.clear()
        flushJob?.cancel()
    }

    override fun onDestroy() {
        super.onDestroy()
        scope.cancel()
    }

    override fun onNotificationPosted(sbn: StatusBarNotification?) {
        val n = sbn ?: return
        scope.launch { handlePosted(n) }
    }

    private suspend fun handlePosted(sbn: StatusBarNotification) {
        // Fail-closed: if the toggle is off, do nothing.
        val enabled = runCatching { prefs.snapshot().notificationsEnabled }
            .getOrDefault(false)
        if (!enabled) return

        if (!shouldForward(sbn)) return

        val mapped = mapToDto(sbn) ?: return
        pending.add(mapped)
        scheduleFlush()
    }

    private fun shouldForward(sbn: StatusBarNotification): Boolean {
        val pkg = sbn.packageName ?: return false

        // Skip our own.
        if (pkg == packageName) return false

        // Hard block payment / banking.
        if (PaymentBlocklist.isBlocked(pkg)) return false

        val n = sbn.notification ?: return false

        // Group summaries are empty shells (the children already post).
        if ((n.flags and Notification.FLAG_GROUP_SUMMARY) != 0) return false

        // Ongoing foreground-service notifications (music, nav, download
        // progress). High-volume, zero Jarvis signal.
        if ((n.flags and Notification.FLAG_ONGOING_EVENT) != 0) return false

        return true
    }

    private fun mapToDto(sbn: StatusBarNotification): NotificationIn? {
        val extras = sbn.notification?.extras ?: return null
        val title = extras.getCharSequence(Notification.EXTRA_TITLE)?.toString().orEmpty()
        val text = (
            extras.getCharSequence(Notification.EXTRA_TEXT)
                ?: extras.getCharSequence(Notification.EXTRA_BIG_TEXT)
            )?.toString().orEmpty()

        // Drop completely empty notifications — nothing useful for Claude.
        if (title.isBlank() && text.isBlank()) return null

        val appName = runCatching {
            val pm = packageManager
            val info = pm.getApplicationInfo(sbn.packageName, 0)
            pm.getApplicationLabel(info).toString()
        }.getOrDefault(sbn.packageName)

        return NotificationIn(
            key = sbn.key ?: "${sbn.packageName}:${sbn.id}:${sbn.postTime}",
            `package` = sbn.packageName,
            appName = appName,
            title = title.take(200),
            text = text.take(1000),
            category = sbn.notification?.category.orEmpty(),
            postedAt = sbn.postTime / 1000.0,
            isOngoing = (sbn.notification?.flags ?: 0) and
                Notification.FLAG_ONGOING_EVENT != 0,
        )
    }

    private fun scheduleFlush() {
        if (flushJob?.isActive == true) return
        flushJob = scope.launch {
            delay(FLUSH_DEBOUNCE_MS)
            flushMutex.withLock { flushNow() }
        }
    }

    private suspend fun flushNow() {
        if (pending.isEmpty()) return
        val batch = buildList {
            repeat(MAX_BATCH) {
                val n = pending.poll() ?: return@buildList
                add(n)
            }
        }
        if (batch.isEmpty()) return

        runCatching {
            api.ingestNotifications(NotificationIngestRequest(notifications = batch))
        }.onFailure {
            // Swallow — we don't retry notification uploads. Missing a
            // notification is acceptable; double-uploading because of a flaky
            // retry would pollute the 30-min window. Backend dedupes anyway.
        }
    }

    private companion object {
        const val FLUSH_DEBOUNCE_MS = 1_500L
        const val MAX_BATCH = 25
    }
}
