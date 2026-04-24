package io.sahay.voice

import android.content.Context
import android.media.AudioAttributes
import android.media.MediaPlayer
import android.util.Log
import dagger.hilt.android.qualifiers.ApplicationContext
import io.sahay.BuildConfig
import io.sahay.data.secrets.SecureStore
import java.io.File
import javax.inject.Inject
import javax.inject.Singleton
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.suspendCancellableCoroutine
import kotlinx.coroutines.withContext
import kotlin.coroutines.resume
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import org.json.JSONObject

/**
 * Streams ElevenLabs MP3 audio from our backend and plays it. The API
 * key stays server-side — this class only holds the user's bearer token.
 *
 * Latency budget (Turbo v2.5 + 5G + our proxy):
 *   Time-to-first-byte  ≈ 250 ms (ElevenLabs)
 *   Server proxy hop    ≈  40 ms
 *   OkHttp → temp file  ≈ 100 ms for short utterances
 *   MediaPlayer prep    ≈ 100 ms
 *   ── total before audio starts ≈ 500 ms for short phrases.
 *
 * For a proper progressive stream (first chunk playing while later chunks
 * still download) we'd switch to ExoPlayer with a custom DataSource.
 * That's Round 7.5 if this still feels laggy.
 *
 * Barge-in: [stop] cancels both the in-flight HTTP call AND releases the
 * MediaPlayer. Called by WakeWordService when the user says the wake
 * phrase while Sahay is talking.
 */
@Singleton
class RemoteTtsPlayer @Inject constructor(
    @ApplicationContext private val context: Context,
    private val store: SecureStore,
) {

    private val http = OkHttpClient.Builder()
        .connectTimeout(java.time.Duration.ofSeconds(5))
        .readTimeout(java.time.Duration.ofSeconds(30))
        .writeTimeout(java.time.Duration.ofSeconds(5))
        .build()

    @Volatile private var current: MediaPlayer? = null
    @Volatile private var currentCall: okhttp3.Call? = null

    val isPlaying: Boolean
        get() = current?.let {
            try { it.isPlaying } catch (_: IllegalStateException) { false }
        } ?: false

    /**
     * Fetch + play. Returns true iff we started playback.
     *
     * @param whisper Halve the volume — matches the Android-TTS whisper
     *                mode used during Quiet Hours.
     */
    suspend fun speak(text: String, voiceId: String? = null, whisper: Boolean = false): Boolean {
        if (text.isBlank()) return false

        val file = downloadToTempFile(text, voiceId) ?: return false

        return withContext(Dispatchers.Main) { startPlayback(file, whisper) }
    }

    /** Cancel any in-flight request and stop playback immediately. */
    fun stop() {
        currentCall?.let { runCatching { it.cancel() } }
        currentCall = null
        current?.let { mp ->
            runCatching { mp.stop() }
            runCatching { mp.release() }
        }
        current = null
    }

    // ─── Internals ────────────────────────────────────────────────────

    private suspend fun downloadToTempFile(
        text: String, voiceId: String?,
    ): File? = withContext(Dispatchers.IO) {
        val token = store.accessToken
        if (token.isNullOrBlank()) {
            Log.w(TAG, "no_access_token — falling back to local TTS")
            return@withContext null
        }

        val payload = JSONObject().apply {
            put("text", text)
            voiceId?.let { put("voice_id", it) }
        }.toString()

        val req = Request.Builder()
            .url("${BuildConfig.API_BASE_URL}/v1/tts/speak")
            .header("Authorization", "Bearer $token")
            .header("Accept", "audio/mpeg")
            .post(payload.toRequestBody(JSON))
            .build()

        val call = http.newCall(req).also { currentCall = it }

        val out = File.createTempFile("sahay-tts-", ".mp3", context.cacheDir).apply {
            deleteOnExit()
        }

        try {
            call.execute().use { resp ->
                if (!resp.isSuccessful) {
                    Log.w(TAG, "tts_http_${resp.code}")
                    out.delete()
                    return@withContext null
                }
                resp.body?.byteStream()?.use { input ->
                    out.outputStream().use { output ->
                        input.copyTo(output, bufferSize = 8 * 1024)
                    }
                } ?: run {
                    out.delete()
                    return@withContext null
                }
            }
        } catch (e: java.io.IOException) {
            if (!call.isCanceled()) Log.w(TAG, "tts_fetch_failed ${e.javaClass.simpleName}: ${e.message}")
            out.delete()
            return@withContext null
        } finally {
            currentCall = null
        }

        if (out.length() <= 0) {
            out.delete()
            return@withContext null
        }
        out
    }

    private fun startPlayback(file: File, whisper: Boolean): Boolean {
        // Kill any prior playback before starting a new one.
        stop()

        val mp = MediaPlayer()
        current = mp
        return try {
            mp.setAudioAttributes(
                AudioAttributes.Builder()
                    .setUsage(AudioAttributes.USAGE_ASSISTANT)
                    .setContentType(AudioAttributes.CONTENT_TYPE_SPEECH)
                    .build()
            )
            mp.setDataSource(file.absolutePath)
            val vol = if (whisper) 0.3f else 1.0f
            mp.setVolume(vol, vol)
            mp.setOnCompletionListener {
                runCatching { it.release() }
                if (current === it) current = null
                runCatching { file.delete() }
            }
            mp.setOnErrorListener { player, what, extra ->
                Log.w(TAG, "mediaplayer_error what=$what extra=$extra")
                runCatching { player.release() }
                if (current === player) current = null
                runCatching { file.delete() }
                true
            }
            mp.prepare()
            mp.start()
            true
        } catch (t: Throwable) {
            Log.w(TAG, "mediaplayer_start_failed: ${t.message}")
            runCatching { mp.release() }
            if (current === mp) current = null
            runCatching { file.delete() }
            false
        }
    }

    private companion object {
        const val TAG = "RemoteTts"
        val JSON = "application/json; charset=utf-8".toMediaType()
    }
}
