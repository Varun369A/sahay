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
    val wakeWordEnabled: Boolean = false,
    val wakeKeyword: String = "computer",
    val builtinKeywords: List<String> = UserPreferences.BUILTIN_KEYWORDS,
    val isLoading: Boolean = false,
    val deleted: Boolean = false,
    val errorKey: String? = null,
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
                    wakeKeyword = snap.wakeKeyword,
                )
            }
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
                _state.update { it.copy(isLoading = false, me = me, usage = usage, grants = grants) }
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
        viewModelScope.launch { prefs.setAssistantName(name) }
    }

    fun setWakeKeyword(keyword: String) {
        _state.update { it.copy(wakeKeyword = keyword) }
        viewModelScope.launch {
            prefs.setWakeKeyword(keyword)
            // Restart wake service so new keyword takes effect.
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

    fun deleteMyData() {
        viewModelScope.launch {
            runCatching { repo.deleteMyData() }
                .onSuccess { _state.update { it.copy(deleted = true) } }
                .onFailure { t -> _state.update { it.copy(errorKey = t.message) } }
        }
    }
}
