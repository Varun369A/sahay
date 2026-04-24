package io.sahay.engine

/**
 * Turns a raw notification into a short, natural spoken sentence.
 *
 * Design choices:
 *   • ZERO network latency — runs on-device in-process. Haiku fallback would
 *     add 500–800ms per announcement which is too slow for an "ambient
 *     assistant" feel.
 *   • Templated per-app for the common cases (WhatsApp, Gmail, Phone, SMS)
 *     because hand-written phrasing reads more human than an LLM paraphrase
 *     on these patterns.
 *   • Natural pacing — sentences include commas to give Android TTS
 *     breathable pauses, which makes it sound less robotic.
 *   • First name only. Using the full contact name every time sounds stilted
 *     ("Message from Mr. Abhilash Krishnan"); people don't talk that way.
 *
 * Inputs:
 *   appPackage — raw package (e.g. "com.whatsapp").
 *   appName    — label resolved on-device ("WhatsApp").
 *   title      — notification title; usually the contact's name.
 *   text       — notification body; the message.
 *   category   — Android's Notification.CATEGORY_* when available.
 *
 * Output:
 *   A single sentence ready for TTS.speak(). Never contains PII tokens;
 *   the listener already scrubs upstream at ingest.
 */
object NotificationHumanizer {

    fun humanize(
        appPackage: String,
        appName: String,
        title: String,
        text: String,
        category: String,
    ): String {
        val who = firstName(title)
        val body = cleanedBody(text)

        return when {
            isPhoneCall(appPackage, category) ->
                if (who.isNotBlank()) "$who is calling." else "Incoming call."

            isWhatsApp(appPackage) ->
                whatsappSentence(who, body)

            isGmail(appPackage) ->
                gmailSentence(who, title, body)

            isSms(appPackage, category) ->
                if (who.isNotBlank() && body.isNotBlank()) "$who texted, $body"
                else if (who.isNotBlank()) "Message from $who."
                else "You have a new text."

            isCalendar(appPackage, category) ->
                if (body.isNotBlank()) "Reminder, $body" else "Calendar reminder."

            else -> genericSentence(appName, title, body)
        }.trimSpeech()
    }

    // ─── Per-app composition ─────────────────────────────────────────

    private fun whatsappSentence(who: String, body: String): String = when {
        body.isBlank() && who.isNotBlank() -> "$who messaged on WhatsApp."
        body.isBlank() -> "New WhatsApp message."
        who.isNotBlank() -> "$who says, $body"
        else -> "WhatsApp message, $body"
    }

    private fun gmailSentence(who: String, title: String, body: String): String {
        // Gmail titles are often the sender; the subject sits in body.
        val subject = body.ifBlank { title }
        return when {
            who.isNotBlank() && subject.isNotBlank() -> "New email from $who, $subject"
            who.isNotBlank() -> "New email from $who."
            else -> "New email."
        }
    }

    private fun genericSentence(appName: String, title: String, body: String): String = when {
        title.isNotBlank() && body.isNotBlank() -> "$appName, $title, $body"
        title.isNotBlank() -> "$appName, $title"
        body.isNotBlank() -> "$appName, $body"
        else -> "New notification from $appName."
    }

    // ─── Cleaning heuristics ─────────────────────────────────────────

    /** Grab the first meaningful word of the title as a first name. */
    private fun firstName(title: String): String {
        val cleaned = title
            .substringBefore("(")                  // WhatsApp group titles: "John (3 messages)"
            .substringBefore("·")
            .trim()
        val first = cleaned.split(Regex("[\\s,]+")).firstOrNull().orEmpty()
        // Skip obvious non-names ("You", "Me", system tags).
        if (first.equals("you", true) || first.equals("me", true)) return cleaned
        return first
    }

    /**
     * Strip inline sender prefixes that TTS would otherwise read twice
     * ("Amirtha: meeting at four" → "meeting at four").
     */
    private fun cleanedBody(text: String): String {
        val idx = text.indexOf(':')
        if (idx in 1..24) {
            val prefix = text.substring(0, idx)
            val looksLikeName = prefix.split(' ').size <= 3 &&
                prefix.all { it.isLetter() || it == ' ' || it == '.' }
            if (looksLikeName) return text.substring(idx + 1).trim()
        }
        return text.trim()
    }

    /** Keep sentences to a speak-friendly length, prefer clean boundary. */
    private fun String.trimSpeech(maxChars: Int = 180): String {
        val collapsed = this.replace(Regex("\\s+"), " ").trim()
        if (collapsed.length <= maxChars) return ensureTerminal(collapsed)
        val cut = collapsed.substring(0, maxChars)
        val lastBoundary = maxOf(cut.lastIndexOf('.'), cut.lastIndexOf(','), cut.lastIndexOf(' '))
        val base = if (lastBoundary > 60) cut.substring(0, lastBoundary) else cut
        return ensureTerminal(base.trim()) + " and more"
    }

    private fun ensureTerminal(s: String): String =
        if (s.isEmpty()) s
        else if (s.last() in ".!?") s
        else "$s."

    // ─── Package/category checks ─────────────────────────────────────

    private fun isPhoneCall(pkg: String, category: String): Boolean =
        category.equals("call", true) || pkg in PHONE_PACKAGES

    private fun isWhatsApp(pkg: String): Boolean =
        pkg == "com.whatsapp" || pkg == "com.whatsapp.w4b"

    private fun isGmail(pkg: String): Boolean =
        pkg == "com.google.android.gm"

    private fun isSms(pkg: String, category: String): Boolean =
        category.equals("msg", true) && pkg in SMS_PACKAGES

    private fun isCalendar(pkg: String, category: String): Boolean =
        category.equals("reminder", true) ||
            category.equals("event", true) ||
            pkg in CALENDAR_PACKAGES

    private val PHONE_PACKAGES = setOf(
        "com.google.android.dialer",
        "com.android.dialer",
        "com.samsung.android.dialer",
        "com.oneplus.dialer",
    )
    private val SMS_PACKAGES = setOf(
        "com.google.android.apps.messaging",
        "com.samsung.android.messaging",
        "com.android.messaging",
    )
    private val CALENDAR_PACKAGES = setOf(
        "com.google.android.calendar",
        "com.samsung.android.calendar",
    )
}
