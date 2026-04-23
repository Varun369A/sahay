package io.sahay.ui.screens.settings

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import dagger.hilt.android.lifecycle.HiltViewModel
import io.sahay.data.api.AppGrantOut
import io.sahay.data.api.MeResponse
import io.sahay.data.api.UsageOut
import io.sahay.data.repository.AppRepository
import io.sahay.engine.PaymentBlocklist
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
    val isLoading: Boolean = false,
    val deleted: Boolean = false,
    val errorKey: String? = null,
)

@HiltViewModel
class SettingsViewModel @Inject constructor(
    private val repo: AppRepository,
) : ViewModel() {

    private val _state = MutableStateFlow(SettingsState())
    val state: StateFlow<SettingsState> = _state.asStateFlow()

    init { refresh() }

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

    fun deleteMyData() {
        viewModelScope.launch {
            runCatching { repo.deleteMyData() }
                .onSuccess { _state.update { it.copy(deleted = true) } }
                .onFailure { t -> _state.update { it.copy(errorKey = t.message) } }
        }
    }
}
