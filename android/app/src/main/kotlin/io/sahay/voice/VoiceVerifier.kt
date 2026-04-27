package io.sahay.voice

import android.util.Log
import javax.inject.Inject
import javax.inject.Singleton
import kotlin.math.abs
import kotlin.math.ln
import kotlin.math.max
import kotlin.math.sqrt

/**
 * Voice-ID verification stage for the wake-word loop.
 *
 * Given a captured PCM audio buffer (the user just said something
 * starting with the wake word), decide whether it's the enrolled user
 * speaking or some other source (TV audio, a different person, an
 * unintentional trigger from background speech).
 *
 * Phase 1 (this file) — pure-Kotlin energy + zero-crossing-rate
 * fingerprinting. Per ~30ms frame we compute:
 *
 *   • log-RMS energy   (loud vs quiet, breath vs voice)
 *   • zero-crossing rate (rough proxy for spectral centroid — vowels
 *                        vs sibilants, voiced vs unvoiced)
 *
 * Comparison is Pearson correlation between the per-frame feature
 * vectors of (captured, enrolled). We compare against EVERY enrolled
 * sample and take the max — handles natural prosody variation.
 *
 * What this catches:
 *   • TV audio with completely different acoustic profile
 *   • Children / opposite-gender voices (different pitch envelope
 *     drives different energy + ZCR patterns)
 *   • Background noise that triggered the wake word in error
 *
 * What it DOESN'T catch (Phase-2 work with a real speaker-embedding
 * model):
 *   • A family member with similar voice characteristics
 *   • Same speaker but different recording environment
 *
 * Default threshold (`ACCEPT_CORRELATION`) is intentionally lenient
 * (0.3) so legitimate triggers aren't blocked. The wake-word loop
 * already has WakeTelemetry's auto-strict mode + AcousticContext as
 * additional layers — verifier is a refinement, not a hard gate.
 */
@Singleton
class VoiceVerifier @Inject constructor(
    private val enrollment: VoiceEnrollment,
) {

    sealed class Result {
        data object NoEnrollment : Result()       // pass — verifier disabled
        data class Accepted(val similarity: Float) : Result()
        data class Rejected(val similarity: Float) : Result()
    }

    /**
     * Verify a captured PCM buffer (16kHz mono 16-bit) against enrolled
     * samples. Returns `NoEnrollment` when the user hasn't enrolled —
     * caller treats that as "accept" (graceful degradation).
     */
    fun verify(capturedPcm: ByteArray): Result {
        val enrolled = enrollment.samples()
        if (enrolled.isEmpty()) return Result.NoEnrollment

        val capturedFeatures = extractFeatures(capturedPcm) ?: return Result.NoEnrollment

        var best = Float.NEGATIVE_INFINITY
        for (sampleFile in enrolled) {
            val sampleBytes = runCatching { sampleFile.readBytes() }.getOrNull() ?: continue
            val sampleFeatures = extractFeatures(sampleBytes) ?: continue
            val sim = pearson(capturedFeatures, sampleFeatures)
            if (sim > best) best = sim
        }
        if (best == Float.NEGATIVE_INFINITY) {
            // No enrolled sample produced features (corrupt files) —
            // don't reject the user on infrastructure failure.
            return Result.NoEnrollment
        }
        Log.i(TAG, "verify max_similarity=${"%.3f".format(best)}")
        return if (best >= ACCEPT_CORRELATION) Result.Accepted(best) else Result.Rejected(best)
    }

    // ── Feature extraction ─────────────────────────────────────────

    /**
     * Returns a flat float vector of [logEnergy, zcr, logEnergy, zcr, ...]
     * across ~30ms frames with 50% overlap. Null if the input is too
     * short to produce at least `MIN_FRAMES` frames.
     *
     * 16-bit PCM little-endian assumed (Android `AudioRecord` default
     * for `ENCODING_PCM_16BIT`).
     */
    private fun extractFeatures(pcm: ByteArray): FloatArray? {
        if (pcm.size < FRAME_BYTES * MIN_FRAMES) return null
        // Truncate to the first ANALYSIS_WINDOW_MS so we compare
        // wake-word-shaped windows; remaining audio is irrelevant.
        val analysisLen = minOf(pcm.size, ANALYSIS_BYTES)
        val features = ArrayList<Float>((analysisLen / FRAME_HOP_BYTES) * 2)
        var offset = 0
        while (offset + FRAME_BYTES <= analysisLen) {
            val (energy, zcr) = frameFeatures(pcm, offset)
            features += energy
            features += zcr
            offset += FRAME_HOP_BYTES
        }
        return if (features.size >= MIN_FRAMES * 2) features.toFloatArray() else null
    }

    private fun frameFeatures(pcm: ByteArray, offset: Int): Pair<Float, Float> {
        var sumSq = 0.0
        var crossings = 0
        var prevSample = 0
        var i = offset
        val end = offset + FRAME_BYTES
        var firstSample = true
        while (i < end - 1) {
            // Little-endian 16-bit signed
            val lo = pcm[i].toInt() and 0xff
            val hi = pcm[i + 1].toInt()
            val s = (hi shl 8) or lo
            sumSq += (s * s).toDouble()
            if (!firstSample) {
                if ((prevSample >= 0) != (s >= 0)) crossings++
            }
            firstSample = false
            prevSample = s
            i += 2
        }
        val samplesInFrame = (FRAME_BYTES / 2).toDouble()
        val rms = sqrt(sumSq / samplesInFrame)
        // log(1 + rms) avoids -inf at silence.
        val logEnergy = ln(1.0 + rms).toFloat()
        val zcr = (crossings / samplesInFrame).toFloat()
        return logEnergy to zcr
    }

    // ── Similarity ────────────────────────────────────────────────

    /**
     * Pearson correlation between two equal-length-or-padded vectors.
     * If lengths differ, both are truncated to the shorter — handles
     * prosody differences (one sample slightly faster/slower) by
     * accepting the shared prefix.
     */
    private fun pearson(a: FloatArray, b: FloatArray): Float {
        val n = minOf(a.size, b.size)
        if (n < 4) return 0f
        var meanA = 0f; var meanB = 0f
        for (i in 0 until n) { meanA += a[i]; meanB += b[i] }
        meanA /= n; meanB /= n
        var num = 0f; var ssA = 0f; var ssB = 0f
        for (i in 0 until n) {
            val da = a[i] - meanA
            val db = b[i] - meanB
            num += da * db
            ssA += da * da
            ssB += db * db
        }
        val denom = sqrt(max(1e-9f, ssA) * max(1e-9f, ssB))
        return if (denom > 0f) (num / denom) else 0f
    }

    private companion object {
        const val TAG = "VoiceVerifier"
        const val SAMPLE_RATE_HZ = 16_000
        const val BYTES_PER_SAMPLE = 2

        // 30ms frames → 480 samples → 960 bytes
        const val FRAME_MS = 30
        const val FRAME_BYTES = SAMPLE_RATE_HZ * FRAME_MS / 1000 * BYTES_PER_SAMPLE
        // 50% hop → 15ms = 480 bytes
        const val FRAME_HOP_BYTES = FRAME_BYTES / 2

        // We compare on the first 1.2 seconds — the wake word + a hair
        // of trailing context. Longer windows dilute the per-frame
        // signal with the actual command.
        const val ANALYSIS_WINDOW_MS = 1_200
        const val ANALYSIS_BYTES =
            SAMPLE_RATE_HZ * ANALYSIS_WINDOW_MS / 1000 * BYTES_PER_SAMPLE

        // Need at least this many frames to compute a stable correlation.
        const val MIN_FRAMES = 8

        // Pearson correlation threshold for acceptance. Calibrated
        // lenient — Pearson on log-energy + ZCR is noisy compared to a
        // learned embedding, and we'd rather a Phase-1 verifier let one
        // false trigger through than reject a legit user.
        const val ACCEPT_CORRELATION = 0.30f
    }

    @Suppress("unused")
    private fun absForLink(x: Float) = abs(x)   // silences unused-import on `kotlin.math.abs`
}
