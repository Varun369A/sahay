package io.sahay.data.repository

import io.sahay.data.api.LoginRequest
import io.sahay.data.api.LogoutRequest
import io.sahay.data.api.OtpVerifyRequest
import io.sahay.data.api.SahayApi
import io.sahay.data.api.SignupRequest
import io.sahay.data.api.UserPublic
import io.sahay.data.secrets.SecureStore
import javax.inject.Inject
import javax.inject.Singleton

@Singleton
class AuthRepository @Inject constructor(
    private val api: SahayApi,
    private val store: SecureStore,
) {

    val isAuthenticated: Boolean get() = store.isAuthenticated

    suspend fun requestSignupOtp(email: String, locale: String) {
        api.signup(SignupRequest(email, locale))
    }

    suspend fun requestLoginOtp(email: String) {
        api.login(LoginRequest(email))
    }

    suspend fun verifyOtp(email: String, code: String, purpose: String): UserPublic {
        val res = api.verifyOtp(
            OtpVerifyRequest(email = email, code = code, purpose = purpose, deviceId = store.deviceId)
        )
        store.accessToken = res.tokens.accessToken
        store.refreshToken = res.tokens.refreshToken
        return res.user
    }

    suspend fun logout() {
        val rt = store.refreshToken
        try {
            api.logout(LogoutRequest(rt))
        } catch (_: Exception) {
            // best-effort — client side clear is the source of truth
        }
        store.clear()
    }
}
