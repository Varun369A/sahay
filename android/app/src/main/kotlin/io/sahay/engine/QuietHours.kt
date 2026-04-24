package io.sahay.engine

import java.time.LocalTime

/**
 * Quiet Hours window logic.
 *
 * A window is defined by `startMin` and `endMin` — minutes-of-day in [0, 1440).
 * The window may wrap past midnight (e.g. 22:00 → 07:00). When start == end,
 * the window is considered disabled (never in quiet hours).
 *
 * We keep this as a plain object so it's testable without any Android
 * framework dependency — [isActiveAt] takes an injected time for tests.
 */
object QuietHours {

    /** True iff the given "now" falls inside the window [startMin, endMin). */
    fun isActiveAt(
        nowMinOfDay: Int,
        startMin: Int,
        endMin: Int,
    ): Boolean {
        if (startMin == endMin) return false
        return if (startMin < endMin) {
            // Simple intra-day window (e.g. 13:00–15:00).
            nowMinOfDay in startMin until endMin
        } else {
            // Wraps midnight (e.g. 22:00–07:00).
            nowMinOfDay >= startMin || nowMinOfDay < endMin
        }
    }

    fun isActiveNow(startMin: Int, endMin: Int, now: LocalTime = LocalTime.now()): Boolean =
        isActiveAt(now.hour * 60 + now.minute, startMin, endMin)

    /**
     * Whether a notification should announce aloud given the current policy.
     *
     * Rules:
     *   • proactive announce disabled → never.
     *   • not in Quiet Hours → announce if the contact is on the important list
     *     (we've already filtered non-important senders upstream in the listener).
     *   • in Quiet Hours → announce only if senderImportance >= dndThreshold.
     *
     * `senderImportance` is -1 when the sender isn't in the user's important
     * contacts list; 1..10 when matched (importance stored in long-term memory).
     */
    fun shouldAnnounce(
        proactiveEnabled: Boolean,
        inQuietHours: Boolean,
        senderImportance: Int,
        dndThreshold: Int,
    ): Boolean {
        if (!proactiveEnabled) return false
        if (!inQuietHours) return senderImportance >= 1
        return senderImportance >= dndThreshold
    }

    /** Minute-of-day → "HH:mm" for UI copy. */
    fun formatHm(minutes: Int): String {
        val h = (minutes / 60).coerceIn(0, 23)
        val m = (minutes % 60).coerceIn(0, 59)
        return "%02d:%02d".format(h, m)
    }
}
