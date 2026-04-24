package io.sahay.data.repository

import io.sahay.data.api.AppGrantOut
import io.sahay.data.api.FileOut
import io.sahay.data.api.GenerateImageRequest
import io.sahay.data.api.GrantAppRequest
import io.sahay.data.api.MeResponse
import io.sahay.data.api.SahayApi
import io.sahay.data.api.UpdateMeRequest
import io.sahay.data.api.UsageOut
import javax.inject.Inject
import javax.inject.Singleton

@Singleton
class AppRepository @Inject constructor(private val api: SahayApi) {

    suspend fun me(): MeResponse = api.me()
    suspend fun updateMe(
        displayName: String? = null,
        locale: String? = null,
        role: String? = null,
    ): MeResponse = api.updateMe(UpdateMeRequest(displayName, locale, role))
    suspend fun deleteMyData() = api.deleteMyData()

    suspend fun listGrants(): List<AppGrantOut> = api.listGrants()
    suspend fun grantApp(pkg: String): AppGrantOut = api.grantApp(GrantAppRequest(pkg))
    suspend fun revokeApp(pkg: String) = api.revokeApp(pkg)

    suspend fun usage(): UsageOut = api.usage()

    suspend fun listFiles(folder: String = "/"): List<FileOut> = api.listFiles(folder)
    suspend fun generateImage(prompt: String, folder: String = "/", name: String? = null): FileOut =
        api.generateImage(GenerateImageRequest(prompt = prompt, folder = folder, name = name))
    suspend fun deleteFile(id: String) = api.deleteFile(id)
}
