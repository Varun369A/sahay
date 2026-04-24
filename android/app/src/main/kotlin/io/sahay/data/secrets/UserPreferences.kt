package io.sahay.data.secrets

import android.content.Context
import androidx.datastore.preferences.core.booleanPreferencesKey
import androidx.datastore.preferences.core.edit
import androidx.datastore.preferences.core.intPreferencesKey
import androidx.datastore.preferences.core.longPreferencesKey
import androidx.datastore.preferences.core.stringPreferencesKey
import androidx.datastore.preferences.preferencesDataStore
import dagger.hilt.android.qualifiers.ApplicationContext
import javax.inject.Inject
import javax.inject.Singleton
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.flow.map

private val Context.dataStore by preferencesDataStore(name = "sahay_user_prefs")

/**
 * User-tunable preferences. Backed by DataStore Preferences (async-friendly,
 * survives process death). Distinct from SecureStore which holds auth tokens.
 *
 * Stored:
 *   - Assistant display name (default "Sahay") — used in copy + future TTS
 *   - Wake word enabled toggle (default false until user grants)
 *   - Wake keyword choice (default "computer" — built-in Picovoice keyword)
 *   - Voice locale hint (default "en-IN")
 */
@Singleton
class UserPreferences @Inject constructor(@ApplicationContext private val context: Context) {

    private object Keys {
        val AssistantName = stringPreferencesKey("assistant_name")
        val WakeWordEnabled = booleanPreferencesKey("wake_word_enabled")
        val VoiceLocale = stringPreferencesKey("voice_locale")
        val NotificationsEnabled = booleanPreferencesKey("notifications_enabled")

        // Proactive voice announcements ("message from boss", "Amirtha is calling")
        val ProactiveAnnounceEnabled = booleanPreferencesKey("proactive_announce_enabled")

        // Quiet Hours window (minutes-of-day, 0..1439). If start == end, window is disabled.
        val QuietHoursStartMin = intPreferencesKey("quiet_hours_start_min")
        val QuietHoursEndMin = intPreferencesKey("quiet_hours_end_min")

        // During Quiet Hours, only notifications whose contact importance
        // is >= this value will announce. 10 = only the most critical people.
        val DndImportanceThreshold = intPreferencesKey("dnd_importance_threshold")

        // Whisper mode: during Quiet Hours lower TTS volume rather than mute.
        val QuietHoursWhisper = booleanPreferencesKey("quiet_hours_whisper")

        // Morning briefing — spoken on first unlock of the day.
        val MorningBriefingEnabled = booleanPreferencesKey("morning_briefing_enabled")
        val LastBriefingAtMs = longPreferencesKey("last_briefing_at_ms")
    }

    val assistantName: Flow<String> = context.dataStore.data.map {
        it[Keys.AssistantName] ?: "Sahay"
    }
    val wakeWordEnabled: Flow<Boolean> = context.dataStore.data.map {
        // Default to ON — user spoke the name, the app listens.
        it[Keys.WakeWordEnabled] ?: true
    }
    val voiceLocale: Flow<String> = context.dataStore.data.map {
        it[Keys.VoiceLocale] ?: "en-IN"
    }
    val notificationsEnabled: Flow<Boolean> = context.dataStore.data.map {
        // Default OFF until the user explicitly grants the notification
        // listener permission — we do NOT want to silently ship
        // notifications off-device before consent.
        it[Keys.NotificationsEnabled] ?: false
    }
    val proactiveAnnounceEnabled: Flow<Boolean> = context.dataStore.data.map {
        it[Keys.ProactiveAnnounceEnabled] ?: false
    }
    val quietHoursStartMin: Flow<Int> = context.dataStore.data.map {
        it[Keys.QuietHoursStartMin] ?: 22 * 60           // 22:00 default
    }
    val quietHoursEndMin: Flow<Int> = context.dataStore.data.map {
        it[Keys.QuietHoursEndMin] ?: 7 * 60              // 07:00 default
    }
    val dndImportanceThreshold: Flow<Int> = context.dataStore.data.map {
        it[Keys.DndImportanceThreshold] ?: 8             // only importance≥8 breaks through
    }
    val quietHoursWhisper: Flow<Boolean> = context.dataStore.data.map {
        it[Keys.QuietHoursWhisper] ?: true               // whisper > mute by default
    }
    val morningBriefingEnabled: Flow<Boolean> = context.dataStore.data.map {
        it[Keys.MorningBriefingEnabled] ?: false         // off until user opts in
    }
    val lastBriefingAtMs: Flow<Long> = context.dataStore.data.map {
        it[Keys.LastBriefingAtMs] ?: 0L
    }

    suspend fun setAssistantName(value: String) = context.dataStore.edit {
        it[Keys.AssistantName] = value.ifBlank { "Sahay" }
    }
    suspend fun setWakeWordEnabled(value: Boolean) = context.dataStore.edit {
        it[Keys.WakeWordEnabled] = value
    }
    suspend fun setVoiceLocale(value: String) = context.dataStore.edit {
        it[Keys.VoiceLocale] = value
    }
    suspend fun setNotificationsEnabled(value: Boolean) = context.dataStore.edit {
        it[Keys.NotificationsEnabled] = value
    }
    suspend fun setProactiveAnnounceEnabled(value: Boolean) = context.dataStore.edit {
        it[Keys.ProactiveAnnounceEnabled] = value
    }
    suspend fun setQuietHoursStartMin(value: Int) = context.dataStore.edit {
        it[Keys.QuietHoursStartMin] = value.coerceIn(0, 24 * 60 - 1)
    }
    suspend fun setQuietHoursEndMin(value: Int) = context.dataStore.edit {
        it[Keys.QuietHoursEndMin] = value.coerceIn(0, 24 * 60 - 1)
    }
    suspend fun setDndImportanceThreshold(value: Int) = context.dataStore.edit {
        it[Keys.DndImportanceThreshold] = value.coerceIn(1, 10)
    }
    suspend fun setQuietHoursWhisper(value: Boolean) = context.dataStore.edit {
        it[Keys.QuietHoursWhisper] = value
    }
    suspend fun setMorningBriefingEnabled(value: Boolean) = context.dataStore.edit {
        it[Keys.MorningBriefingEnabled] = value
    }
    suspend fun setLastBriefingAtMs(value: Long) = context.dataStore.edit {
        it[Keys.LastBriefingAtMs] = value
    }

    suspend fun snapshot(): Snapshot {
        val data = context.dataStore.data.first()
        return Snapshot(
            assistantName = data[Keys.AssistantName] ?: "Sahay",
            wakeWordEnabled = data[Keys.WakeWordEnabled] ?: true,
            voiceLocale = data[Keys.VoiceLocale] ?: "en-IN",
            notificationsEnabled = data[Keys.NotificationsEnabled] ?: false,
            proactiveAnnounceEnabled = data[Keys.ProactiveAnnounceEnabled] ?: false,
            quietHoursStartMin = data[Keys.QuietHoursStartMin] ?: 22 * 60,
            quietHoursEndMin = data[Keys.QuietHoursEndMin] ?: 7 * 60,
            dndImportanceThreshold = data[Keys.DndImportanceThreshold] ?: 8,
            quietHoursWhisper = data[Keys.QuietHoursWhisper] ?: true,
            morningBriefingEnabled = data[Keys.MorningBriefingEnabled] ?: false,
            lastBriefingAtMs = data[Keys.LastBriefingAtMs] ?: 0L,
        )
    }

    data class Snapshot(
        val assistantName: String,
        val wakeWordEnabled: Boolean,
        val voiceLocale: String,
        val notificationsEnabled: Boolean,
        val proactiveAnnounceEnabled: Boolean = false,
        val quietHoursStartMin: Int = 22 * 60,
        val quietHoursEndMin: Int = 7 * 60,
        val dndImportanceThreshold: Int = 8,
        val quietHoursWhisper: Boolean = true,
        val morningBriefingEnabled: Boolean = false,
        val lastBriefingAtMs: Long = 0L,
    )
}
