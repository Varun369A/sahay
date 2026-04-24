package io.sahay.data.api

import retrofit2.http.Body
import retrofit2.http.DELETE
import retrofit2.http.GET
import retrofit2.http.PATCH
import retrofit2.http.POST
import retrofit2.http.Path
import retrofit2.http.Query

interface SahayApi {

    // ─── Auth ────────────────────────────────────────────────────────
    @POST("/v1/auth/signup")
    suspend fun signup(@Body body: SignupRequest): OtpSentResponse

    @POST("/v1/auth/login")
    suspend fun login(@Body body: LoginRequest): OtpSentResponse

    @POST("/v1/auth/verify-otp")
    suspend fun verifyOtp(@Body body: OtpVerifyRequest): AuthResponse

    @POST("/v1/auth/refresh")
    suspend fun refresh(@Body body: RefreshRequest): TokenPair

    @POST("/v1/auth/logout")
    suspend fun logout(@Body body: LogoutRequest)

    // ─── Me + Grants ─────────────────────────────────────────────────
    @GET("/v1/me")
    suspend fun me(): MeResponse

    @PATCH("/v1/me")
    suspend fun updateMe(@Body body: UpdateMeRequest): MeResponse

    @DELETE("/v1/me")
    suspend fun deleteMyData()

    @GET("/v1/me/app-grants")
    suspend fun listGrants(): List<AppGrantOut>

    @POST("/v1/me/app-grants")
    suspend fun grantApp(@Body body: GrantAppRequest): AppGrantOut

    @DELETE("/v1/me/app-grants/{appPackage}")
    suspend fun revokeApp(@Path("appPackage") appPackage: String)

    // ─── Playbooks ───────────────────────────────────────────────────
    @POST("/v1/playbooks/lookup")
    suspend fun lookupPlaybook(@Body body: PlaybookLookupRequest): PlaybookOut

    // ─── Actions ─────────────────────────────────────────────────────
    @POST("/v1/actions/plan")
    suspend fun planAction(@Body body: PlanRequest): PlanResponse

    @POST("/v1/actions/dispatch")
    suspend fun dispatchUtterance(@Body body: DispatchRequest): DispatchResponse

    @POST("/v1/actions/resolve")
    suspend fun resolveStep(@Body body: ResolveRequest): ResolveResponse

    @POST("/v1/agent/step")
    suspend fun agentStep(@Body body: AgentStepRequest): AgentStepResponse

    @POST("/v1/actions/log")
    suspend fun logAction(@Body body: ActionLogRequest)

    // ─── Usage ───────────────────────────────────────────────────────
    @GET("/v1/usage")
    suspend fun usage(): UsageOut

    // ─── Files ───────────────────────────────────────────────────────
    @POST("/v1/files/generate-image")
    suspend fun generateImage(@Body body: GenerateImageRequest): FileOut

    @PATCH("/v1/files/{id}/edit")
    suspend fun editImage(@Path("id") id: String, @Body body: EditImageRequest): FileOut

    @GET("/v1/files")
    suspend fun listFiles(@Query("folder") folder: String = "/", @Query("limit") limit: Int = 50): List<FileOut>

    @DELETE("/v1/files/{id}")
    suspend fun deleteFile(@Path("id") id: String)
}
