package io.sahay.ui.screens.settings

import android.content.Context
import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import dagger.hilt.android.lifecycle.HiltViewModel
import dagger.hilt.android.qualifiers.ApplicationContext
import io.sahay.data.api.AppGrantOut
import io.sahay.data.api.MeResponse
import io.sahay.data.api.UsageOut
import io.sahay.data.repository.AppRepository
import io.sahay.data.secrets.UserPreferences
import io.sahay.engine.PaymentBlocklist
import io.sahay.service.WakeWordService
import io.sahay.util.Permissions
import javax.inject.Inject
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch

data class SettingsState(
    val me: MeResponse? = null,
    val usage: UsageOut? = null,
    val grants: List<AppGrantOut> = emptyList(),
    val assistantName: String = "Sahay",
    val wakeWordEnabled: Boolean = true,
    val notificationsEnabled: Boolean = false,
    val notificationListenerGranted: Boolean = false,
    val proactiveAnnounceEnabled: Boolean = false,
    val quietHoursStartMin: Int = 22 * 60,
    val quietHoursEndMin: Int = 7 * 60,
    val dndImportanceThreshold: Int = 8,
    val quietHoursWhisper: Boolean = true,
    val morningBriefingEnabled: Boolean = false,
    val screenshotUploadEnabled: Boolean = false,
    val role: String? = null,
    val mood: String? = null,
    val isLoading: Boolean = false,
    val deleted: Boolean = false,
    val errorKey: String? = null,
)

/**
 * Roles the user can pick. Must stay in sync with `ROLE_VALUES` in
 * `backend/src/sahay/schemas/user.py`. Ordered to put the most common
 * picks (in our target market) at the front so the horizontal-scroll
 * chip row feels immediate.
 */
val SAHAY_ROLES: List<Pair<String, String>> = listOf(
    "student" to "Student",
    "engineer" to "Engineer",
    "founder" to "Founder",
    "manager" to "Manager",
    "sales" to "Sales",
    "designer" to "Designer",
    "doctor" to "Doctor",
    "teacher" to "Teacher",
    "writer" to "Writer",
    "lawyer" to "Lawyer",
    "researcher" to "Researcher",
    "other" to "Other",
)

/** Mood = how the assistant TALKS. Kept in sync with MOOD_VALUES on backend. */
val SAHAY_MOODS: List<Triple<String, String, String>> = listOf(
    Triple("default", "Balanced", "Warm + efficient. The safe default."),
    Triple("playful", "Playful", "Dry humour, Jarvis-style quips."),
    Triple("warm", "Warm", "Encouraging, friendly, explicit."),
    Triple("focused", "Focused", "Ruthless efficiency. No small talk."),
    Triple("formal", "Formal", "Precise, respectful. 'Sir / madam'."),
)

@HiltViewModel
class SettingsViewModel @Inject constructor(
    @ApplicationContext private val context: Context,
    private val repo: AppRepository,
    private val prefs: UserPreferences,
) : ViewModel() {

    private val _state = MutableStateFlow(SettingsState())
    val state: StateFlow<SettingsState> = _state.asStateFlow()

    init {
        refresh()
        viewModelScope.launch {
            val snap = prefs.snapshot()
            _state.update {
                it.copy(
                    assistantName = snap.assistantName,
                    wakeWordEnabled = snap.wakeWordEnabled,
                    notificationsEnabled = snap.notificationsEnabled,
                    notificationListenerGranted =
                        Permissions.isNotificationListenerEnabled(context),
                    proactiveAnnounceEnabled = snap.proactiveAnnounceEnabled,
                    quietHoursStartMin = snap.quietHoursStartMin,
                    quietHoursEndMin = snap.quietHoursEndMin,
                    dndImportanceThreshold = snap.dndImportanceThreshold,
                    quietHoursWhisper = snap.quietHoursWhisper,
                    morningBriefingEnabled = snap.morningBriefingEnabled,
                    screenshotUploadEnabled = snap.screenshotUploadEnabled,
                )
            }
        }
    }

    /** Re-check the listener grant when the user returns from system settings. */
    fun refreshPermissionGrants() {
        _state.update {
            it.copy(
                notificationListenerGranted =
                    Permissions.isNotificationListenerEnabled(context),
            )
        }
    }

    fun refresh() {
        viewModelScope.launch {
            _state.update { it.copy(isLoading = true, errorKey = null) }
            runCatching {
                val me = repo.me()
                val usage = runCatching { repo.usage() }.getOrNull()
                val grants = runCatching { repo.listGrants() }.getOrDefault(emptyList())
                Triple(me, usage, grants)
            }.onSuccess { (me, usage, grants) ->
                _state.update {
                    it.copy(
                        isLoading = false, me = me, usage = usage, grants = grants,
                        role = me.role,
                        mood = me.mood ?: "default",
                    )
                }
            }.onFailure { t ->
                _state.update { it.copy(isLoading = false, errorKey = t.message) }
            }
        }
    }

    fun grant(pkg: String) {
        if (PaymentBlocklist.isBlocked(pkg)) {
            _state.update { it.copy(errorKey = "payment_app_blocked") }
            return
        }
        viewModelScope.launch {
            runCatching { repo.grantApp(pkg) }.onSuccess { refresh() }
        }
    }

    fun revoke(pkg: String) {
        viewModelScope.launch {
            runCatching { repo.revokeApp(pkg) }.onSuccess { refresh() }
        }
    }

    fun setAssistantName(name: String) {
        _state.update { it.copy(assistantName = name) }
        viewModelScope.launch {
            prefs.setAssistantName(name)
            // Restart wake service so the new name becomes the wake phrase.
            if (_state.value.wakeWordEnabled) {
                WakeWordService.stop(context)
                WakeWordService.start(context)
            }
        }
    }

    fun setWakeWordEnabled(enabled: Boolean) {
        _state.update { it.copy(wakeWordEnabled = enabled) }
        viewModelScope.launch {
            prefs.setWakeWordEnabled(enabled)
            if (enabled) WakeWordService.start(context) else WakeWordService.stop(context)
        }
    }

    fun setNotificationsEnabled(enabled: Boolean) {
        _state.update { it.copy(notificationsEnabled = enabled) }
        viewModelScope.launch { prefs.setNotificationsEnabled(enabled) }
    }

    fun openNotificationListenerSettings() {
        Permissions.openNotificationListenerSettings(context)
    }

    fun setProactiveAnnounceEnabled(value: Boolean) {
        _state.update { it.copy(proactiveAnnounceEnabled = value) }
        viewModelScope.launch { prefs.setProactiveAnnounceEnabled(value) }
    }

    fun setQuietHoursStart(totalMin: Int) {
        _state.update { it.copy(quietHoursStartMin = totalMin) }
        viewModelScope.launch { prefs.setQuietHoursStartMin(totalMin) }
    }

    fun setQuietHoursEnd(totalMin: Int) {
        _state.update { it.copy(quietHoursEndMin = totalMin) }
        viewModelScope.launch { prefs.setQuietHoursEndMin(totalMin) }
    }

    fun setDndImportanceThreshold(value: Int) {
        _state.update { it.copy(dndImportanceThreshold = value) }
        viewModelScope.launch { prefs.setDndImportanceThreshold(value) }
    }

    fun setQuietHoursWhisper(value: Boolean) {
        _state.update { it.copy(quietHoursWhisper = value) }
        viewModelScope.launch { prefs.setQuietHoursWhisper(value) }
    }

    fun setMorningBriefingEnabled(value: Boolean) {
        _state.update { it.copy(morningBriefingEnabled = value) }
        viewModelScope.launch { prefs.setMorningBriefingEnabled(value) }
    }

    fun setScreenshotUploadEnabled(value: Boolean) {
        _state.update { it.copy(screenshotUploadEnabled = value) }
        viewModelScope.launch { prefs.setScreenshotUploadEnabled(value) }
    }

    fun setRole(roleKey: String) {
        // Optimistic local update so the selected chip flips immediately;
        // revert on server failure so the UI never lies.
        val previous = _state.value.role
        _state.update { it.copy(role = roleKey) }
        viewModelScope.launch {
            runCatching { repo.updateMe(role = roleKey) }
                .onFailure { t ->
                    _state.update { it.copy(role = previous, errorKey = t.message) }
                }
        }
    }

    fun setMood(moodKey: String) {
        val previous = _state.value.mood
        _state.update { it.copy(mood = moodKey) }
        viewModelScope.launch {
            runCatching { repo.updateMe(mood = moodKey) }
                .onFailure { t ->
                    _state.update { it.copy(mood = previous, errorKey = t.message) }
                }
        }
    }

    fun deleteMyData() {
        viewModelScope.launch {
            runCatching { repo.deleteMyData() }
                .onSuccess { _state.update { it.copy(deleted = true) } }
                .onFailure { t -> _state.update { it.copy(errorKey = t.message) } }
        }
    }
}
