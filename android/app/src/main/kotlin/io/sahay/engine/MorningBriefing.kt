package io.sahay.engine

import android.util.Log
import io.sahay.data.api.SahayApi
import io.sahay.data.secrets.UserPreferences
import io.sahay.voice.TtsClient
import java.util.Calendar
import javax.inject.Inject
import javax.inject.Singleton
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock

/**
 * First-unlock-of-the-day briefing trigger.
 *
 * Invoked from [io.sahay.service.SahayForegroundService]'s ACTION_USER_PRESENT
 * receiver on every screen unlock. We only actually speak a briefing when:
 *
 *   • The user has opted in (morningBriefingEnabled).
 *   • Local clock is inside a morning window (default 05:00 – 11:00). Outside
 *     the window we skip — briefings are *morning* briefings, not "whenever
 *     you unlocked after lunch".
 *   • The last briefing was delivered more than MIN_GAP_MS ago. This catches
 *     the common pattern where the user taps lock → unlock repeatedly in the
 *     morning; we brief once and stay quiet the rest of the day.
 *   • We're online. We don't queue briefings — a late briefing reads as
 *     stale ambient noise rather than useful.
 *
 * All predicates are cheap-and-private. Errors inside the network call are
 * swallowed silently — a missed briefing is strictly better than an error-
 * message one.
 */
@Singleton
class MorningBriefing @Inject constructor(
    private val prefs: UserPreferences,
    private val api: SahayApi,
    private val tts: TtsClient,
) {

    /** Serialises back-to-back unlocks — guards against the "two fast unlocks
     *  both fire the briefing" race. */
    private val mutex = Mutex()

    /**
     * Evaluate the gate. If we should speak a briefing, fetch it and
     * announce it. Returns the Outcome so callers can log; never throws.
     */
    suspend fun maybeAnnounce(): Outcome = mutex.withLock {
        val snap = runCatching { prefs.snapshot() }.getOrNull()
            ?: return@withLock Outcome.SKIPPED_PREFS_UNAVAILABLE

        if (!snap.morningBriefingEnabled) return@withLock Outcome.SKIPPED_DISABLED

        val now = System.currentTimeMillis()
        val cal = Calendar.getInstance().apply { timeInMillis = now }
        val hour = cal.get(Calendar.HOUR_OF_DAY)
        if (hour !in WINDOW_START_HOUR until WINDOW_END_HOUR) {
            return@withLock Outcome.SKIPPED_OUT_OF_WINDOW
        }

        if (now - snap.lastBriefingAtMs < MIN_GAP_MS) {
            return@withLock Outcome.SKIPPED_RECENTLY_DELIVERED
        }

        // Best-effort fetch + speak. Any failure is silent by design.
        val briefing = runCatching { api.morningBriefing() }.getOrNull()
            ?: return@withLock Outcome.SKIPPED_FETCH_FAILED

        val text = briefing.text.trim()
        if (text.isEmpty()) return@withLock Outcome.SKIPPED_EMPTY

        tts.init()
        tts.announce(text, whisper = false)

        runCatching { prefs.setLastBriefingAtMs(now) }

        Log.i(TAG, "briefing_spoken chars=${text.length} model=${briefing.model} " +
            "web=${briefing.webSearches} cost_paise=${briefing.costInrPaise}")
        return@withLock Outcome.SPOKEN
    }

    enum class Outcome {
        SPOKEN,
        SKIPPED_DISABLED,
        SKIPPED_OUT_OF_WINDOW,
        SKIPPED_RECENTLY_DELIVERED,
        SKIPPED_FETCH_FAILED,
        SKIPPED_EMPTY,
        SKIPPED_PREFS_UNAVAILABLE,
    }

    private companion object {
        const val TAG = "MorningBriefing"
        const val WINDOW_START_HOUR = 5
        const val WINDOW_END_HOUR = 11
        // 8h gap so a re-unlock in the same morning doesn't re-announce, but
        // a next-day unlock at 6am (≈22h later) will.
        const val MIN_GAP_MS = 8L * 60L * 60L * 1000L
    }
}
