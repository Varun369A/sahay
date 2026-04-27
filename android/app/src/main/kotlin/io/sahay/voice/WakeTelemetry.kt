package io.sahay.voice

import android.content.Context
import androidx.datastore.preferences.core.edit
import androidx.datastore.preferences.core.intPreferencesKey
import androidx.datastore.preferences.preferencesDataStore
import dagger.hilt.android.qualifiers.ApplicationContext
import javax.inject.Inject
import javax.inject.Singleton
import kotlinx.coroutines.flow.first

private val Context.wakeTelemetryStore by preferencesDataStore(name = "sahay_wake_telemetry")

/**
 * Per-device wake-word telemetry + auto-tuning sensitivity knob.
 *
 * Why this exists: a wake word that fires once when the user said it
 * AND once for the TV is twice as bad as a wake word that misses 30%
 * of real triggers. Users tolerate "I had to say it twice" far better
 * than "it activated while I was watching a show."
 *
 * Signal: a triggered capture whose STT result is BLANK (or contains
 * only the wake word with no follow-up command) is almost certainly a
 * false positive. We record those, compute a rolling FP rate, and
 * tighten matching when it gets out of hand.
 *
 * Tightening strategy (single knob): in strict mode, the wake loop
 * matches only on FINAL recognition results, never on partials. Final
 * results imply Vosk has heard a clean stop after the phrase; partials
 * fire mid-utterance and are the dominant source of false positives
 * from background speech.
 *
 * Storage: tiny — two ints in a dedicated DataStore namespace, persisted
 * across process restart so the FP rate isn't reset every reboot.
 */
@Singleton
class WakeTelemetry @Inject constructor(
    @ApplicationContext private val context: Context,
) {

    private object Keys {
        val Total      = intPreferencesKey("wake_total")
        val FalsePos   = intPreferencesKey("wake_false_pos")
    }

    /**
     * Record one wake event.
     *
     * @param accepted true if the captured utterance produced a non-empty
     *   command (real trigger); false if the STT came back blank or the
     *   user only said the wake word (likely false positive).
     */
    suspend fun recordTrigger(accepted: Boolean) {
        context.wakeTelemetryStore.edit { prefs ->
            prefs[Keys.Total] = (prefs[Keys.Total] ?: 0) + 1
            if (!accepted) {
                prefs[Keys.FalsePos] = (prefs[Keys.FalsePos] ?: 0) + 1
            }
        }
    }

    /**
     * Current observed false-positive rate. Returns 0 when we haven't
     * seen enough data to be confident either way (saturating at the
     * minimum sample size keeps us in lenient mode for new installs).
     */
    suspend fun falsePositiveRate(): Float {
        val data = context.wakeTelemetryStore.data.first()
        val total = data[Keys.Total] ?: 0
        val fp = data[Keys.FalsePos] ?: 0
        if (total < MIN_SAMPLES_FOR_TUNING) return 0f
        return fp.toFloat() / total.toFloat()
    }

    /**
     * Snapshot used by the wake loop on every match attempt. Async-loaded
     * once at service start (cheap, non-blocking) and cached for the
     * loop's lifetime — re-read after each capture so changes take
     * effect within a few seconds.
     */
    suspend fun matchMode(): MatchMode {
        val rate = falsePositiveRate()
        return if (rate >= STRICT_THRESHOLD) MatchMode.STRICT else MatchMode.LENIENT
    }

    enum class MatchMode {
        /** Fire on partial OR final recognition. Default for new installs. */
        LENIENT,
        /** Fire ONLY on final recognition. Auto-engaged when FP rate ≥ 30%. */
        STRICT,
    }

    /** Visible for testing — clear all telemetry (e.g. user reset). */
    suspend fun reset() {
        context.wakeTelemetryStore.edit { prefs ->
            prefs[Keys.Total] = 0
            prefs[Keys.FalsePos] = 0
        }
    }

    private companion object {
        // Don't tune until we have at least this many samples — random
        // variance at low N would flap the user between modes.
        const val MIN_SAMPLES_FOR_TUNING = 20

        // Switch to STRICT when 30%+ of triggers are false positives.
        // Field tuning: below 30% the missed-real-trigger cost of strict
        // mode outweighs the FP cost; above 30% it flips.
        const val STRICT_THRESHOLD = 0.30f
    }
}
