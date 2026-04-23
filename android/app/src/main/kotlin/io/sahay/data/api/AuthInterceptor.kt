package io.sahay.data.api

import io.sahay.data.secrets.SecureStore
import kotlinx.coroutines.runBlocking
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.serialization.json.Json
import okhttp3.Interceptor
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import okhttp3.Response
import okhttp3.ResponseBody.Companion.toResponseBody
import javax.inject.Inject
import javax.inject.Singleton

/**
 * Adds Bearer token to every request.
 * On 401, attempts a one-time refresh and retries.
 *
 * Single-flight refresh: many concurrent requests should not cause many refresh calls.
 */
@Singleton
class AuthInterceptor @Inject constructor(
    private val store: SecureStore,
    private val refreshApi: RefreshOnlyApi,
    private val json: Json,
) : Interceptor {

    private val refreshMutex = Mutex()

    override fun intercept(chain: Interceptor.Chain): Response {
        val original = chain.request()
        if (original.url.encodedPath.startsWith("/v1/auth/")) {
            return chain.proceed(original)
        }

        val withAuth = original.attachToken(store.accessToken)
        val first = chain.proceed(withAuth)
        if (first.code != 401) return first

        // Try to refresh.
        first.close()
        val refreshed = runBlocking {
            refreshMutex.withLock {
                val rt = store.refreshToken ?: return@withLock false
                try {
                    val newPair = refreshApi.refresh(RefreshRequest(rt))
                    store.accessToken = newPair.accessToken
                    store.refreshToken = newPair.refreshToken
                    true
                } catch (_: Exception) {
                    store.clear()
                    false
                }
            }
        }

        if (!refreshed) {
            return errorResponse(original, code = 401, message = "auth_expired")
        }

        return chain.proceed(original.attachToken(store.accessToken))
    }

    private fun Request.attachToken(token: String?): Request {
        if (token.isNullOrBlank()) return this
        return newBuilder().header("Authorization", "Bearer $token").build()
    }

    private fun errorResponse(req: Request, code: Int, message: String): Response =
        Response.Builder()
            .request(req)
            .protocol(okhttp3.Protocol.HTTP_1_1)
            .code(code)
            .message(message)
            .body("""{"error":"$message"}""".toResponseBody("application/json".toMediaType()))
            .build()
}

/**
 * Tiny separate API used INSIDE the AuthInterceptor to avoid dependency cycles
 * (the main SahayApi depends on the OkHttp client which has the interceptor).
 */
interface RefreshOnlyApi {
    @retrofit2.http.POST("/v1/auth/refresh")
    suspend fun refresh(@retrofit2.http.Body body: RefreshRequest): TokenPair
}
