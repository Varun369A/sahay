package io.sahay.engine

import io.sahay.data.api.PlanResponse
import io.sahay.data.api.SahayApi
import javax.inject.Inject
import javax.inject.Singleton
import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonObject

/**
 * Turns a free-text utterance ("send Amirtha hi") into a structured
 * (app_package, intent, inputs) tuple by asking the backend planner to
 * select among known playbooks.
 *
 * For v1 we keep the parser server-side via a special dispatch playbook
 * lookup; the device sends the raw utterance, and the server returns either
 * a parsed PlanResponse or a clarification request.
 *
 * The "dispatch" call is a normal /v1/actions/plan with intent="dispatch_utterance"
 * and inputs={"utterance": "..."}. The server's playbook for this intent
 * picks the best matching app+intent.
 */
@Singleton
class IntentParser @Inject constructor(private val api: SahayApi, private val json: Json) {

    @Serializable
    data class ParsedIntent(
        @SerialName("app_package") val appPackage: String,
        val intent: String,
        val inputs: JsonObject,
        val confidence: Double = 0.5,
    )

    /**
     * Returns a fully-resolved PlanResponse OR null if intent unrecognised.
     *
     * Note: in the current backend the dispatcher playbook isn't seeded yet
     * so this falls back to client-side keyword matching. Replace with
     * server-side dispatch once /v1/dispatch lands.
     */
    suspend fun parse(utterance: String): ParsedIntent? {
        val u = utterance.lowercase().trim()
        return when {
            "whatsapp" in u && ("send" in u || "message" in u) -> ParsedIntent(
                appPackage = "com.whatsapp",
                intent = "send_message",
                inputs = jsonObjectOf(
                    "recipient_name" to extractAfter(utterance, listOf("to", "send")),
                    "message_text" to extractAfter(utterance, listOf("message", "saying", "text")),
                ),
            )
            "youtube" in u && ("play" in u || "search" in u) -> ParsedIntent(
                appPackage = "com.google.android.youtube",
                intent = "search_and_play",
                inputs = jsonObjectOf("query" to extractAfter(utterance, listOf("for", "play", "search"))),
            )
            "netflix" in u && ("play" in u || "search" in u || "watch" in u) -> ParsedIntent(
                appPackage = "com.netflix.mediaclient",
                intent = "search_and_play",
                inputs = jsonObjectOf("title" to extractAfter(utterance, listOf("for", "watch", "play"))),
            )
            ("navigate" in u || "directions" in u || "go to" in u) -> ParsedIntent(
                appPackage = "com.google.android.apps.maps",
                intent = "navigate_to",
                inputs = jsonObjectOf("destination" to extractAfter(utterance, listOf("to"))),
            )
            ("note" in u || "remind" in u) -> ParsedIntent(
                appPackage = "com.google.android.keep",
                intent = "create_note",
                inputs = jsonObjectOf("title" to "Voice note", "body" to utterance),
            )
            ("post on linkedin" in u || "linkedin post" in u) -> ParsedIntent(
                appPackage = "com.linkedin.android",
                intent = "post_update",
                inputs = jsonObjectOf("post_text" to extractAfter(utterance, listOf("saying", "with", ":"))),
            )
            ("dm" in u && "instagram" in u) -> ParsedIntent(
                appPackage = "com.instagram.android",
                intent = "send_dm",
                inputs = jsonObjectOf(
                    "recipient_username" to extractAfter(utterance, listOf("to", "dm")),
                    "message_text" to extractAfter(utterance, listOf("message", "saying")),
                ),
            )
            ("event" in u || "calendar" in u || "schedule" in u) -> ParsedIntent(
                appPackage = "com.google.android.calendar",
                intent = "create_event",
                inputs = jsonObjectOf("title" to utterance, "datetime_iso" to ""),
            )
            ("email" in u || "gmail" in u) -> ParsedIntent(
                appPackage = "com.google.android.gm",
                intent = "compose_email",
                inputs = jsonObjectOf(
                    "recipient_email" to extractAfter(utterance, listOf("to")),
                    "subject" to extractAfter(utterance, listOf("subject", "about")),
                    "body" to extractAfter(utterance, listOf("body", "saying")),
                ),
            )
            else -> null
        }
    }

    private fun jsonObjectOf(vararg pairs: Pair<String, String>): JsonObject {
        val obj = mutableMapOf<String, kotlinx.serialization.json.JsonElement>()
        pairs.forEach { (k, v) -> obj[k] = kotlinx.serialization.json.JsonPrimitive(v) }
        return JsonObject(obj)
    }

    private fun extractAfter(text: String, markers: List<String>): String {
        val lower = text.lowercase()
        for (m in markers) {
            val i = lower.indexOf(" $m ")
            if (i >= 0) {
                val rest = text.substring(i + m.length + 2).trim()
                if (rest.isNotBlank()) return rest
            }
        }
        return text
    }
}
