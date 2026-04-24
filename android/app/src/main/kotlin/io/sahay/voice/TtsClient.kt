package io.sahay.voice

import android.content.Context
import android.os.Bundle
import android.speech.tts.TextToSpeech
import android.speech.tts.UtteranceProgressListener
import android.util.Log
import dagger.hilt.android.qualifiers.ApplicationContext
import java.util.Locale
import java.util.concurrent.atomic.AtomicBoolean
import javax.inject.Inject
import javax.inject.Singleton
import kotlin.coroutines.resume
import kotlinx.coroutines.suspendCancellableCoroutine
import kotlinx.coroutines.withTimeoutOrNull

/**
 * Wrapper around Android's built-in TextToSpeech tuned for a calm, human
 * narrator tone — lower pitch, slightly slower rate than default. The
 * default Android voice is flat and robotic; these values soften it.
 *
 * Provides two surfaces:
 *   speak()    — suspending, waits until playback ends. Used by the agent
 *                `speak` tool where the loop cares when the utterance finishes.
 *   announce() — fire-and-forget, used by the notification listener for
 *                proactive announcements. Supports whisper mode (lower
 *                volume for Quiet Hours).
 *
 * For premium voices (Pro tier) we can swap in ElevenLabs later — the public
 * API here stays the same.
 */
@Singleton
class TtsClient @Inject constructor(@ApplicationContext private val context: Context) {

    private val ready = AtomicBoolean(false)
    private var engine: TextToSpeech? = null

    fun init(locale: Locale = Locale("en", "IN")) {
        if (engine != null) return
        engine = TextToSpeech(context) { status ->
            if (status == TextToSpeech.SUCCESS) {
                engine?.language = locale
                // Lower pitch = less nasal. Slower rate = perceptible pauses
                // between phrases. Tuned by ear on Google TTS and Samsung TTS.
                engine?.setPitch(PITCH_HUMAN)
                engine?.setSpeechRate(RATE_HUMAN)
                ready.set(true)
                Log.i(TAG, "tts_ready locale=$locale")
            } else {
                Log.e(TAG, "tts_init_failed status=$status")
            }
        }
    }

    /** Speak and suspend until playback completes (or 10s elapse). */
    suspend fun speak(text: String): Boolean {
        if (text.isBlank()) return false
        val e = engine ?: run { init(); return false }      // first call primes engine
        if (!ready.get()) return false

        val id = "sahay_${System.currentTimeMillis()}"
        val completed = withTimeoutOrNull(10_000) {
            suspendCancellableCoroutine<Boolean> { cont ->
                e.setOnUtteranceProgressListener(object : UtteranceProgressListener() {
                    override fun onStart(utteranceId: String?) {}
                    override fun onDone(utteranceId: String?) {
                        if (cont.isActive) cont.resume(true)
                    }
                    @Deprecated("kept for API compat")
                    override fun onError(utteranceId: String?) {
                        if (cont.isActive) cont.resume(false)
                    }
                    override fun onError(utteranceId: String?, errorCode: Int) {
                        if (cont.isActive) cont.resume(false)
                    }
                })
                val rc = e.speak(text, TextToSpeech.QUEUE_ADD, null, id)
                if (rc != TextToSpeech.SUCCESS && cont.isActive) cont.resume(false)
            }
        }
        return completed ?: false
    }

    /**
     * Fire-and-forget announcement. Used by the notification listener when
     * a high-priority message arrives. Returns immediately — we don't want
     * to block the listener's coroutine on full playback.
     *
     * @param whisper When true, speak at reduced volume (~30%) — intended
     *                for Quiet Hours where the user still wants to hear
     *                important messages but not be startled.
     */
    fun announce(text: String, whisper: Boolean = false): Boolean {
        if (text.isBlank()) return false
        val e = engine ?: run { init(); return false }
        if (!ready.get()) return false

        val params = Bundle().apply {
            putFloat(TextToSpeech.Engine.KEY_PARAM_VOLUME, if (whisper) VOLUME_WHISPER else VOLUME_NORMAL)
            putFloat(TextToSpeech.Engine.KEY_PARAM_PAN, 0f)
        }
        val id = "announce_${System.currentTimeMillis()}"
        val rc = e.speak(text, TextToSpeech.QUEUE_ADD, params, id)
        return rc == TextToSpeech.SUCCESS
    }

    fun shutdown() {
        engine?.stop()
        engine?.shutdown()
        engine = null
        ready.set(false)
    }

    private companion object {
        const val TAG = "Tts"

        // Tone — tuned for "calm human narrator" on Google/Samsung TTS.
        const val PITCH_HUMAN = 0.9f
        const val RATE_HUMAN = 0.88f

        const val VOLUME_NORMAL = 1.0f
        const val VOLUME_WHISPER = 0.3f
    }
}
