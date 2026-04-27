package io.sahay.engine

import android.util.Log
import java.security.MessageDigest
import javax.inject.Inject
import javax.inject.Singleton

/**
 * Per-conversation, in-memory dedup for irreversible side-effecting
 * actions (sending a reply, posting, calling, deleting).
 *
 * The risk we close: a network blip, an agent retry, or a model glitch
 * makes the same `reply_to_notification(key=K, text=T)` fire twice — and
 * the user's contact gets the same WhatsApp message twice. The dispatcher
 * itself has no memory; this tracker gives it one.
 *
 * How it's used:
 *
 *   val key = IdempotencyTracker.keyFor("reply_to_notification",
 *                                       conversationId, notificationKey, text)
 *   if (!tracker.checkAndMark(key)) {
 *       // already executed in this conversation — silently skip
 *       return ExecResult("duplicate_skipped")
 *   }
 *   doTheActualSend(...)
 *
 * Scoping rules:
 *   • Keys are stable within a conversation, so a network retry that
 *     surfaces the same tool call fingerprint dedupes.
 *   • Different conversation = different key — re-asking the assistant
 *     to send the message is honored. (We never silently swallow user
 *     intent.)
 *   • Entries expire after `TTL_MS`; a long pause + later retry of the
 *     identical action falls outside the window and DOES re-fire. This
 *     is intentional — past the TTL the user has clearly moved on, and
 *     a deliberate "send again" should work.
 *
 * Memory: bounded by `MAX_ENTRIES`. On overflow we evict the oldest by
 * insertion order. We don't persist across process death — that's fine
 * because retries from another process are themselves a deliberate
 * user action.
 */
@Singleton
class IdempotencyTracker @Inject constructor() {

    private val seen = LinkedHashMap<String, Long>(64, 0.75f, false)

    /**
     * Atomically: check whether `key` was already marked recently, and if
     * not, mark it now. Returns `true` if this caller has the green light
     * to execute (was NOT seen) — `false` means a duplicate that should be
     * skipped.
     */
    @Synchronized
    fun checkAndMark(key: String): Boolean {
        val now = System.currentTimeMillis()
        evictExpired(now)

        val existing = seen[key]
        if (existing != null && now - existing < TTL_MS) {
            Log.i(TAG, "duplicate key=${key.take(16)}… age_ms=${now - existing}")
            return false
        }

        seen[key] = now
        if (seen.size > MAX_ENTRIES) {
            // Evict oldest by insertion order (LinkedHashMap iteration order).
            val it = seen.entries.iterator()
            if (it.hasNext()) { it.next(); it.remove() }
        }
        return true
    }

    /** Visible for testing — clears all state. */
    @Synchronized
    fun reset() { seen.clear() }

    @Synchronized
    private fun evictExpired(now: Long) {
        val cutoff = now - TTL_MS
        val it = seen.entries.iterator()
        while (it.hasNext()) {
            val e = it.next()
            if (e.value < cutoff) it.remove() else break  // ordered → first survivor stops sweep
        }
    }

    companion object {
        private const val TAG = "IdempotencyTracker"
        private const val TTL_MS = 5 * 60 * 1000L     // 5 minutes
        private const val MAX_ENTRIES = 256

        /**
         * Canonical key for a tool dispatch. Uses SHA-256 of all the
         * fields that uniquely identify "this action, with this target,
         * in this conversation". Truncated to 32 hex chars — collision
         * space is enormous given the per-conversation scoping.
         */
        fun keyFor(vararg parts: String?): String {
            val joined = parts.joinToString(separator = "") { it ?: "" }
            val md = MessageDigest.getInstance("SHA-256")
            val digest = md.digest(joined.toByteArray(Charsets.UTF_8))
            return buildString(32) {
                for (i in 0 until 16) {
                    val b = digest[i].toInt() and 0xff
                    append(HEX[b ushr 4])
                    append(HEX[b and 0x0f])
                }
            }
        }

        private val HEX = "0123456789abcdef".toCharArray()
    }
}
