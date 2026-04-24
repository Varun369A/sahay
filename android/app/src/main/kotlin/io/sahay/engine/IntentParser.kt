package io.sahay.engine

import android.util.Log
import io.sahay.data.api.DispatchRequest
import io.sahay.data.api.SahayApi
import javax.inject.Inject
import javax.inject.Singleton
import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.JsonObject

/**
 * Voice utterance → structured (app_package, intent, inputs).
 *
 * Calls the backend /v1/actions/dispatch endpoint, which uses Claude Haiku
 * to handle any language (English, Tamil, Hindi, Tanglish, code-switched).
 *
 * No client-side regex matching — that approach can't handle Indic scripts
 * or natural-language variation.
 */
@Singleton
class IntentParser @Inject constructor(private val api: SahayApi) {

    @Serializable
    data class ParsedIntent(
        @SerialName("app_package") val appPackage: String,
        val intent: String,
        val inputs: JsonObject,
        val confidence: Double = 0.5,
    )

    /**
     * Returns null if Claude couldn't confidently match the utterance to any
     * known playbook. Caller should ask user to clarify.
     */
    suspend fun parse(utterance: String, locale: String = "en"): ParsedIntent? {
        val resp = try {
            api.dispatchUtterance(DispatchRequest(utterance = utterance, locale = locale))
        } catch (t: Throwable) {
            Log.e(TAG, "dispatch_failed", t)
            return null
        }
        if (!resp.matched || resp.appPackage.isNullOrBlank() || resp.intent.isNullOrBlank()) {
            Log.i(TAG, "no_intent_match conf=${resp.confidence} notes=${resp.notes}")
            return null
        }
        Log.i(TAG, "intent=${resp.intent} app=${resp.appPackage} conf=${resp.confidence}")
        return ParsedIntent(
            appPackage = resp.appPackage,
            intent = resp.intent,
            inputs = resp.inputs,
            confidence = resp.confidence,
        )
    }

    private companion object {
        const val TAG = "IntentParser"
    }
}
