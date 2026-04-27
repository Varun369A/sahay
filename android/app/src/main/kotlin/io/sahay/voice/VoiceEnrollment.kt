package io.sahay.voice

import android.content.Context
import android.util.Log
import dagger.hilt.android.qualifiers.ApplicationContext
import java.io.File
import javax.inject.Inject
import javax.inject.Singleton

/**
 * Per-user voice-ID enrollment store.
 *
 * Saves up to `MAX_SAMPLES` short audio clips of the user saying the
 * wake word. The clips live in the app's private files dir
 * (`<files>/voice_enrollment/`) — never uploaded, never visible to other
 * apps, deleted on app uninstall by the OS.
 *
 * Read by `VoiceVerifier` to fingerprint the user's voice on each wake
 * trigger. When `samples()` is empty, the verifier short-circuits to
 * "accept" so wake-word still works for users who haven't enrolled yet
 * (graceful degradation).
 *
 * Future Phase-2 work: replace the raw-PCM samples with extracted
 * speaker embeddings from a small on-device model (~5MB) for sharper
 * verification. The interface here doesn't change — `samples()` still
 * returns the same list; the verifier becomes embedding-based.
 */
@Singleton
class VoiceEnrollment @Inject constructor(
    @ApplicationContext private val context: Context,
) {

    private val dir: File by lazy {
        File(context.filesDir, ENROLLMENT_DIR).apply { mkdirs() }
    }

    /**
     * All enrolled audio clips, oldest first. Empty when the user
     * hasn't enrolled — verifier treats that as "verification disabled."
     */
    fun samples(): List<File> =
        dir.listFiles { f -> f.isFile && f.name.startsWith(SAMPLE_PREFIX) }
            ?.sortedBy { it.lastModified() }
            ?: emptyList()

    val isEnrolled: Boolean get() = samples().size >= MIN_SAMPLES_FOR_ENROLLED

    /**
     * Persist one new enrollment clip. Caller is responsible for the
     * audio format (16kHz mono 16-bit PCM, ~1 second). The store keeps
     * at most `MAX_SAMPLES` — older clips get evicted FIFO so a
     * re-enrollment after voice change (cold, growth, etc.) isn't
     * polluted by stale samples.
     */
    fun addSample(pcmBytes: ByteArray): File {
        val name = "$SAMPLE_PREFIX${System.currentTimeMillis()}.pcm"
        val file = File(dir, name)
        file.writeBytes(pcmBytes)
        Log.i(TAG, "enrolled sample=${file.name} bytes=${pcmBytes.size}")
        evictOldIfNeeded()
        return file
    }

    /** Wipe all enrollment data — used by "forget my voice" in Settings. */
    fun clear() {
        dir.listFiles()?.forEach { it.delete() }
        Log.i(TAG, "cleared all enrollment samples")
    }

    private fun evictOldIfNeeded() {
        val all = samples()
        if (all.size <= MAX_SAMPLES) return
        val toRemove = all.subList(0, all.size - MAX_SAMPLES)
        for (f in toRemove) f.delete()
    }

    private companion object {
        const val TAG = "VoiceEnrollment"
        const val ENROLLMENT_DIR = "voice_enrollment"
        const val SAMPLE_PREFIX = "sample_"
        // Enrollment is considered complete once we have at least this
        // many samples. 3 captures different prosody / speaking rate.
        const val MIN_SAMPLES_FOR_ENROLLED = 3
        // Hard cap so re-enrollment doesn't grow unbounded.
        const val MAX_SAMPLES = 5
    }
}
