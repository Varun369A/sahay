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
import kotlinx.coroutines.launch
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
class TtsClient @Inject constructor(
    @ApplicationContext private val context: Context,
    private val remote: RemoteTtsPlayer,
) {

    private val ready = AtomicBoolean(false)
    private var engine: TextToSpeech? = null
    private val remoteScope = kotlinx.coroutines.CoroutineScope(
        kotlinx.coroutines.SupervisorJob() + kotlinx.coroutines.Dispatchers.IO
    )

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

    /**
     * Speak and suspend until playback completes (or 15s elapse).
     *
     * Tries ElevenLabs first. If remote fails (no key, budget exhausted,
     * network), falls back to on-device Android TTS. Either way the
     * suspension resumes only once audio actually finishes.
     */
    suspend fun speak(text: String): Boolean {
        if (text.isBlank()) return false

        // Try remote first — returns false fast if backend is 503 / 402.
        val remoteOk = runCatching { remote.speak(text) }.getOrDefault(false)
        if (remoteOk) {
            // Remote kicks off MediaPlayer; wait for it to stop.
            // Poll every 100 ms up to 15s — cheap and cancel-safe.
            val deadline = System.currentTimeMillis() + 15_000L
            while (remote.isPlaying && System.currentTimeMillis() < deadline) {
                kotlinx.coroutines.delay(100)
            }
            return true
        }

        // ── Local fallback ──
        val e = engine ?: run { init(); return false }
        if (!ready.get()) return false

        val id = "sahay_${System.currentTimeMillis()}"
        val completed = withTimeoutOrNull(15_000) {
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
     * Fire-and-forget announcement. Prefers ElevenLabs (streamed via our
     * backend proxy) for a calm human voice; falls back to on-device
     * Android TTS if the remote call fails, 503s (key not set), 402s
     * (char budget exhausted), or returns no audio.
     *
     * Returns immediately — we don't want to block the listener's
     * coroutine on full playback.
     *
     * @param whisper When true, speak at reduced volume (~30%) — intended
     *                for Quiet Hours where the user still wants to hear
     *                important messages but not be startled.
     */
    fun announce(text: String, whisper: Boolean = false): Boolean {
        if (text.isBlank()) return false

        // Try the premium path first — silently fall back to Android TTS
        // on any failure. Either way we return true immediately (caller
        // expects fire-and-forget).
        remoteScope.launch {
            val ok = runCatching { remote.speak(text, whisper = whisper) }.getOrDefault(false)
            if (!ok) {
                speakLocal(text, whisper)
            }
        }
        return true
    }

    /**
     * Stop any active playback — used for barge-in (user says the wake
     * word while Sahay is talking). Stops BOTH remote MP3 playback and
     * any queued Android TTS utterances.
     */
    fun stopSpeaking() {
        runCatching { remote.stop() }
        runCatching { engine?.stop() }
    }

    private fun speakLocal(text: String, whisper: Boolean) {
        val e = engine ?: run { init(); return }
        if (!ready.get()) return

        val params = Bundle().apply {
            putFloat(TextToSpeech.Engine.KEY_PARAM_VOLUME, if (whisper) VOLUME_WHISPER else VOLUME_NORMAL)
            putFloat(TextToSpeech.Engine.KEY_PARAM_PAN, 0f)
        }
        val id = "announce_local_${System.currentTimeMillis()}"
        e.speak(text, TextToSpeech.QUEUE_ADD, params, id)
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
