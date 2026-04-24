package io.sahay.data.api

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable

// ─── Auth ────────────────────────────────────────────────────────────

@Serializable
data class SignupRequest(val email: String, val locale: String = "en")

@Serializable
data class LoginRequest(val email: String)

@Serializable
data class OtpVerifyRequest(
    val email: String,
    val code: String,
    val purpose: String,                      // "signup" | "login"
    @SerialName("device_id") val deviceId: String? = null,
)

@Serializable
data class RefreshRequest(@SerialName("refresh_token") val refreshToken: String)

@Serializable
data class LogoutRequest(@SerialName("refresh_token") val refreshToken: String?)

@Serializable
data class TokenPair(
    @SerialName("access_token") val accessToken: String,
    @SerialName("refresh_token") val refreshToken: String,
    @SerialName("token_type") val tokenType: String = "bearer",
    @SerialName("expires_in") val expiresIn: Int,
)

@Serializable
data class UserPublic(
    val id: String,
    val email: String,
    @SerialName("display_name") val displayName: String?,
    val locale: String,
    val tier: String,
    @SerialName("email_verified") val emailVerified: Boolean,
    @SerialName("created_at") val createdAt: String,
)

@Serializable
data class AuthResponse(val user: UserPublic, val tokens: TokenPair)

@Serializable
data class OtpSentResponse(val message: String, @SerialName("expires_in_seconds") val expiresInSeconds: Int)

// ─── Playbooks ───────────────────────────────────────────────────────

@Serializable
data class PlaybookLookupRequest(
    @SerialName("app_package") val appPackage: String,
    val intent: String,
    @SerialName("app_version") val appVersion: String? = null,
)

@Serializable
data class PlaybookOut(
    val id: String,
    @SerialName("app_package") val appPackage: String,
    @SerialName("app_name") val appName: String,
    @SerialName("app_version") val appVersion: String?,
    val intent: String,
    @SerialName("steps_json") val stepsJson: kotlinx.serialization.json.JsonObject,
    @SerialName("reliability_score") val reliabilityScore: Double,
    val popularity: Int,
    @SerialName("updated_at") val updatedAt: String,
)

// ─── Actions ─────────────────────────────────────────────────────────

@Serializable
data class PlanRequest(
    @SerialName("app_package") val appPackage: String,
    val intent: String,
    val inputs: Map<String, kotlinx.serialization.json.JsonElement> = emptyMap(),
    @SerialName("screen_tree_summary") val screenTreeSummary: String? = null,
    @SerialName("app_version") val appVersion: String? = null,
)

@Serializable
data class PlannedStepOut(
    val step: Int,
    val description: String,
    @SerialName("target_screen") val targetScreen: String,
    val action: String,
    @SerialName("target_selector") val targetSelector: kotlinx.serialization.json.JsonObject,
    val params: kotlinx.serialization.json.JsonObject,
    @SerialName("confirm_required") val confirmRequired: Boolean,
    @SerialName("timeout_ms") val timeoutMs: Int,
    @SerialName("on_failure") val onFailure: String? = null,
)

@Serializable
data class PlanResponse(
    val steps: List<PlannedStepOut>,
    val source: String,
    @SerialName("reliability_score") val reliabilityScore: Double,
    @SerialName("needs_per_step_confirm") val needsPerStepConfirm: Boolean,
    @SerialName("requires_inputs") val requiresInputs: List<String> = emptyList(),
    val notes: String? = null,
)

@Serializable
data class DispatchRequest(
    val utterance: String,
    val locale: String = "en",
)

@Serializable
data class DispatchResponse(
    val matched: Boolean,
    @SerialName("app_package") val appPackage: String? = null,
    val intent: String? = null,
    val inputs: kotlinx.serialization.json.JsonObject = kotlinx.serialization.json.JsonObject(emptyMap()),
    val confidence: Double = 0.0,
    val notes: String? = null,
)

@Serializable
data class ResolveRequest(
    @SerialName("app_package") val appPackage: String,
    @SerialName("step_description") val stepDescription: String,
    @SerialName("desired_action") val desiredAction: String,
    @SerialName("screen_tree_summary") val screenTreeSummary: String,
    val inputs: kotlinx.serialization.json.JsonObject = kotlinx.serialization.json.JsonObject(emptyMap()),
)

@Serializable
data class ResolveResponse(
    val found: Boolean,
    val selector: kotlinx.serialization.json.JsonObject = kotlinx.serialization.json.JsonObject(emptyMap()),
    val action: String? = null,
    val params: kotlinx.serialization.json.JsonObject = kotlinx.serialization.json.JsonObject(emptyMap()),
    val confidence: Double = 0.0,
    val notes: String? = null,
)

@Serializable
data class ActionLogRequest(
    @SerialName("app_package") val appPackage: String,
    val intent: String,
    val status: String,                       // success | failed | aborted | needs_confirm
    @SerialName("duration_ms") val durationMs: Int? = null,
    @SerialName("failure_reason") val failureReason: String? = null,
    @SerialName("user_aborted") val userAborted: Boolean = false,
)

// ─── Usage / Me ──────────────────────────────────────────────────────

@Serializable
data class UsageOut(
    @SerialName("daily_count") val dailyCount: Int,
    @SerialName("daily_limit") val dailyLimit: Int,
    @SerialName("weekly_count") val weeklyCount: Int,
    @SerialName("weekly_limit") val weeklyLimit: Int,
    @SerialName("monthly_image_gens_used") val monthlyImageGensUsed: Int,
    @SerialName("monthly_image_gens_limit") val monthlyImageGensLimit: Int,
    val tier: String,
)

@Serializable
data class MeResponse(
    val id: String,
    val email: String,
    @SerialName("display_name") val displayName: String?,
    val locale: String,
    val tier: String,
    @SerialName("email_verified") val emailVerified: Boolean,
    @SerialName("created_at") val createdAt: String,
    @SerialName("last_active_at") val lastActiveAt: String?,
)

@Serializable
data class UpdateMeRequest(
    @SerialName("display_name") val displayName: String? = null,
    val locale: String? = null,
)

@Serializable
data class GrantAppRequest(@SerialName("app_package") val appPackage: String)

@Serializable
data class AppGrantOut(
    @SerialName("app_package") val appPackage: String,
    @SerialName("granted_at") val grantedAt: String,
    @SerialName("revoked_at") val revokedAt: String? = null,
)

// ─── Files ───────────────────────────────────────────────────────────

@Serializable
data class FileOut(
    val id: String,
    val folder: String,
    val name: String,
    @SerialName("mime_type") val mimeType: String,
    @SerialName("size_bytes") val sizeBytes: Long?,
    @SerialName("parent_id") val parentId: String?,
    val source: String,
    @SerialName("created_at") val createdAt: String,
    @SerialName("download_url") val downloadUrl: String? = null,
)

@Serializable
data class GenerateImageRequest(
    val prompt: String,
    val folder: String = "/",
    val name: String? = null,
    @SerialName("aspect_ratio") val aspectRatio: String = "1:1",
    val seed: Int? = null,
)

@Serializable
data class EditImageRequest(val prompt: String)
