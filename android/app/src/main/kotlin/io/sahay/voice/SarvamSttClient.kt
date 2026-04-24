package io.sahay.voice

import android.util.Log
import io.sahay.BuildConfig
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.MultipartBody
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import org.json.JSONObject
import java.io.File
import java.util.concurrent.TimeUnit
import javax.inject.Inject
import javax.inject.Singleton

/**
 * Speech-to-text via Sarvam Speech API.
 * Strong on Indic languages (Tamil, Hindi, etc.).
 *
 * All work happens on Dispatchers.IO. Caller can invoke from any context.
 */
@Singleton
class SarvamSttClient @Inject constructor() {

    private val client = OkHttpClient.Builder()
        .connectTimeout(10, TimeUnit.SECONDS)
        .readTimeout(60, TimeUnit.SECONDS)
        .build()

    suspend fun transcribe(
        wavFile: File,
        languageHint: String? = null,
    ): TranscriptionResult = withContext(Dispatchers.IO) {
        if (BuildConfig.SARVAM_API_KEY.isBlank()) {
            return@withContext TranscriptionResult(
                text = "", language = "unknown", error = "missing_sarvam_key",
            )
        }

        val mediaType = "audio/wav".toMediaType()
        val multipart = MultipartBody.Builder()
            .setType(MultipartBody.FORM)
            .addFormDataPart("file", wavFile.name, wavFile.readBytes().toRequestBody(mediaType))
            .addFormDataPart("model", "saarika:v2.5")
            .also { if (!languageHint.isNullOrBlank()) it.addFormDataPart("language_code", languageHint) }
            .build()

        val req = Request.Builder()
            .url(SARVAM_URL)
            .header("api-subscription-key", BuildConfig.SARVAM_API_KEY)
            .post(multipart)
            .build()

        try {
            client.newCall(req).execute().use { resp ->
                val bodyStr = resp.body?.string().orEmpty()
                if (!resp.isSuccessful) {
                    Log.w(TAG, "sarvam http ${resp.code}: $bodyStr")
                    return@withContext TranscriptionResult(
                        text = "",
                        language = "unknown",
                        error = "http_${resp.code}:${bodyStr.take(120)}",
                    )
                }
                val json = JSONObject(bodyStr)
                val text = json.optString("transcript", "")
                val lang = json.optString("language_code", languageHint ?: "en-IN")
                Log.i(TAG, "sarvam ok lang=$lang chars=${text.length}")
                TranscriptionResult(text = text, language = lang)
            }
        } catch (t: Throwable) {
            Log.e(TAG, "sarvam call failed", t)
            TranscriptionResult(
                text = "",
                language = "unknown",
                error = "exception:${t::class.simpleName}:${t.message ?: "no_message"}",
            )
        }
    }

    data class TranscriptionResult(
        val text: String,
        val language: String,
        val error: String? = null,
    ) {
        val ok: Boolean get() = error == null && text.isNotBlank()
    }

    private companion object {
        const val SARVAM_URL = "https://api.sarvam.ai/speech-to-text"
        const val TAG = "SarvamStt"
    }
}
