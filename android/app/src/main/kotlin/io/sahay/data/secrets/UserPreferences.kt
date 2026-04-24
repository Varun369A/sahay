package io.sahay.data.secrets

import android.content.Context
import androidx.datastore.preferences.core.booleanPreferencesKey
import androidx.datastore.preferences.core.edit
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

    suspend fun setAssistantName(value: String) = context.dataStore.edit {
        it[Keys.AssistantName] = value.ifBlank { "Sahay" }
    }
    suspend fun setWakeWordEnabled(value: Boolean) = context.dataStore.edit {
        it[Keys.WakeWordEnabled] = value
    }
    suspend fun setVoiceLocale(value: String) = context.dataStore.edit {
        it[Keys.VoiceLocale] = value
    }

    suspend fun snapshot(): Snapshot {
        val data = context.dataStore.data.first()
        return Snapshot(
            assistantName = data[Keys.AssistantName] ?: "Sahay",
            wakeWordEnabled = data[Keys.WakeWordEnabled] ?: true,
            voiceLocale = data[Keys.VoiceLocale] ?: "en-IN",
        )
    }

    data class Snapshot(
        val assistantName: String,
        val wakeWordEnabled: Boolean,
        val voiceLocale: String,
    )
}
