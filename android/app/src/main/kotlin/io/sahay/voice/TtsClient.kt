package io.sahay.voice

import android.content.Context
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
 * Simple wrapper around Android's built-in TextToSpeech.
 * Free, on-device on most modern phones, no API key.
 *
 * For premium voices (Pro tier) we can swap in ElevenLabs later;
 * the public API here stays the same.
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
                engine?.setPitch(1.0f)
                engine?.setSpeechRate(1.0f)
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

    fun shutdown() {
        engine?.stop()
        engine?.shutdown()
        engine = null
        ready.set(false)
    }

    private companion object { const val TAG = "Tts" }
}
