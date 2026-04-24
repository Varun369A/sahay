package io.sahay.ui.screens.auth

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import dagger.hilt.android.lifecycle.HiltViewModel
import io.sahay.data.repository.AuthRepository
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import javax.inject.Inject

data class AuthState(
    val step: Step = Step.EmailEntry,
    val email: String = "",
    val locale: String = "en",
    val purpose: String = "signup",
    val code: String = "",
    val isLoading: Boolean = false,
    val errorKey: String? = null,
    val isAuthenticated: Boolean = false,
) {
    enum class Step { EmailEntry, OtpEntry }
}

@HiltViewModel
class AuthViewModel @Inject constructor(
    private val repo: AuthRepository,
) : ViewModel() {

    private val _state = MutableStateFlow(AuthState(isAuthenticated = repo.isAuthenticated))
    val state: StateFlow<AuthState> = _state.asStateFlow()

    fun onEmailChange(value: String) = _state.update { it.copy(email = value, errorKey = null) }
    fun onLocaleChange(value: String) = _state.update { it.copy(locale = value) }
    fun onCodeChange(value: String) = _state.update {
        it.copy(code = value.filter { c -> c.isDigit() }.take(6), errorKey = null)
    }

    fun requestOtp(asLogin: Boolean = false) {
        val email = _state.value.email.trim().lowercase()
        if (!isValidEmail(email)) {
            _state.update { it.copy(errorKey = "invalid_email") }
            return
        }
        val purpose = if (asLogin) "login" else "signup"
        viewModelScope.launch {
            _state.update { it.copy(isLoading = true, errorKey = null) }
            runCatching {
                if (asLogin) repo.requestLoginOtp(email)
                else repo.requestSignupOtp(email, _state.value.locale)
            }.onSuccess {
                _state.update { it.copy(isLoading = false, step = AuthState.Step.OtpEntry, purpose = purpose) }
            }.onFailure { t ->
                _state.update { it.copy(isLoading = false, errorKey = "otp_request_failed:${t.message ?: ""}") }
            }
        }
    }

    fun verifyOtp() {
        val s = _state.value
        if (s.code.length != 6) {
            _state.update { it.copy(errorKey = "code_must_be_6_digits") }
            return
        }
        viewModelScope.launch {
            _state.update { it.copy(isLoading = true, errorKey = null) }
            runCatching { repo.verifyOtp(s.email.trim().lowercase(), s.code, s.purpose) }
                .onSuccess {
                    _state.update { it.copy(isLoading = false, isAuthenticated = true) }
                }
                .onFailure { t ->
                    _state.update { it.copy(isLoading = false, errorKey = "verify_failed:${t.message ?: ""}") }
                }
        }
    }

    fun back() {
        _state.update { it.copy(step = AuthState.Step.EmailEntry, code = "", errorKey = null) }
    }

    fun logout() {
        viewModelScope.launch {
            repo.logout()
            _state.update { AuthState() }
        }
    }

    private fun isValidEmail(s: String): Boolean =
        Regex("^[^@\\s]+@[^@\\s]+\\.[^@\\s]+$").matches(s)
}
