package io.sahay.service

import android.app.RemoteInput
import android.content.Context
import android.content.Intent
import android.os.Bundle
import android.util.Log
import io.sahay.engine.PaymentBlocklist

/**
 * Fires a RemoteInput reply against a specific active notification.
 *
 * Used when the agent emits `reply_to_notification(notification_key, text)`:
 *   1. We find the live StatusBarNotification via the running
 *      [SahayNotificationListenerService].
 *   2. Defense in depth: refuse if the source package is on the payment
 *      blocklist (ingest already filters, but the agent is a second layer
 *      of generation and we want to block here too).
 *   3. Pick the first RemoteInput action on the notification — messaging
 *      apps universally expose a "Reply" action; picking the first one
 *      works for WhatsApp, SMS/Messages, Gmail, Telegram, etc.
 *   4. Pack the agent's text into a results Bundle keyed by the
 *      RemoteInput's `resultKey`, add it to an Intent, then send the
 *      notification's PendingIntent with that extra. The OS hands the
 *      reply to the source app, which sends it through its usual send
 *      pipeline — no app switch, no UI.
 *
 * Returns a short outcome string — used by AgentLoop to record success
 * or a specific failure in turn history.
 */
object NotificationReplyDispatcher {

    private const val TAG = "NotifReply"

    fun dispatch(context: Context, keyPrefix: String, text: String): String {
        if (keyPrefix.isBlank()) return "no_key"
        if (text.isBlank()) return "empty_text"

        val listener = SahayNotificationListenerService.instance
            ?: return "listener_not_running"

        val actives = runCatching { listener.activeNotifications }
            .getOrNull() ?: return "active_list_unavailable"

        val match = actives
            .filter { it.key != null && it.key.startsWith(keyPrefix) }
            .maxByOrNull { it.postTime }
            ?: return "notification_not_found"

        if (PaymentBlocklist.isBlocked(match.packageName)) {
            Log.w(TAG, "blocked_payment_reply pkg=${match.packageName}")
            return "payment_app_blocked"
        }

        val actions = match.notification?.actions
        if (actions.isNullOrEmpty()) return "no_actions"

        val replyAction = actions.firstOrNull { a ->
            val inputs = a.remoteInputs
            inputs != null && inputs.isNotEmpty()
        } ?: return "no_reply_action"

        val remoteInput = replyAction.remoteInputs?.firstOrNull()
            ?: return "no_remote_input"
        val pendingIntent = replyAction.actionIntent ?: return "no_pending_intent"

        val results = Bundle().apply {
            putCharSequence(remoteInput.resultKey, text)
        }
        val fillIntent = Intent()
        RemoteInput.addResultsToIntent(arrayOf(remoteInput), fillIntent, results)

        return try {
            pendingIntent.send(context, 0, fillIntent)
            Log.i(TAG, "reply_sent pkg=${match.packageName} key=${match.key}")
            "ok"
        } catch (e: Exception) {
            Log.e(TAG, "reply_send_failed pkg=${match.packageName}", e)
            "send_failed:${e.javaClass.simpleName}"
        }
    }
}
