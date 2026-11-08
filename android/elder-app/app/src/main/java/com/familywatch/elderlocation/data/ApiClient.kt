package com.familywatch.elderlocation.data

import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.Json
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import java.time.Instant
import java.util.concurrent.TimeUnit

@Serializable data class EnrollBody(
    val code: String,
    @SerialName("installationId") val installationId: String,
    val model: String,
    @SerialName("appVersion") val appVersion: String,
)
@Serializable data class EnrollResponse(
    @SerialName("deviceToken") val deviceToken: String,
    @SerialName("elderId") val elderId: String,
    @SerialName("elderDisplayName") val elderDisplayName: String,
    val timezone: String,
)
@Serializable data class HomeGeofenceResponse(
    val latitude: Double,
    val longitude: Double,
    @SerialName("radiusM") val radiusM: Int,
    @SerialName("updatedAt") val updatedAt: String,
)
@Serializable data class DeviceConfigResponse(
    @SerialName("elderDisplayName") val elderDisplayName: String,
    val timezone: String,
    @SerialName("configVersion") val configVersion: Int = 1,
    @SerialName("homeGeofence") val homeGeofence: HomeGeofenceResponse? = null,
)
@Serializable data class LocationPayload(
    val coordinateSystem: String,
    val sequence: Long,
    @SerialName("capturedAt") val capturedAt: String,
    val latitude: Double,
    val longitude: Double,
    @SerialName("accuracyM") val accuracyM: Float,
    @SerialName("speedMps") val speedMps: Float?,
    @SerialName("batteryPct") val batteryPct: Int,
    @SerialName("isCharging") val isCharging: Boolean,
    @SerialName("isMock") val isMock: Boolean,
    @SerialName("locateRequestId") val locateRequestId: String? = null,
)
@Serializable data class LocationBatch(
    val points: List<LocationPayload>,
    @SerialName("appVersion") val appVersion: String,
)
@Serializable data class Rejected(@SerialName("sequence") val sequence: Long, val reason: String)
@Serializable data class BatchResponse(
    val lastAcceptedAt: String? = null,
    @SerialName("acceptedSequences") val accepted: List<Long> = emptyList(),
    @SerialName("duplicateSequences") val duplicates: List<Long> = emptyList(),
    val rejected: List<Rejected> = emptyList(),
)
@Serializable private data class ApiErrorBody(val detail: String? = null)
@Serializable data class DeviceCommandWaitBody(
    @SerialName("appVersion") val appVersion: String,
    @SerialName("backgroundLocationGranted") val backgroundLocationGranted: Boolean,
    @SerialName("batteryOptimizationIgnored") val batteryOptimizationIgnored: Boolean,
    @SerialName("locationServicesEnabled") val locationServicesEnabled: Boolean,
    @SerialName("serviceError") val serviceError: String? = null,
)
@Serializable data class LocateCommand(
    val id: String,
    val type: String,
    @SerialName("expiresAt") val expiresAt: String,
)
@Serializable private data class LocateFailureBody(
    @SerialName("failureCode") val failureCode: String,
)

class ApiHttpException(
    val statusCode: Int,
    val serverDetail: String?,
    val retryAfterSeconds: Long?,
) : RuntimeException(serverDetail ?: "HTTP $statusCode")

class ApiClient {
    private val json = Json { ignoreUnknownKeys = true }
    private val http = OkHttpClient.Builder()
        .connectTimeout(15, TimeUnit.SECONDS)
        .readTimeout(30, TimeUnit.SECONDS)
        .build()
    private val mediaType = "application/json; charset=utf-8".toMediaType()

    suspend fun stopSharing(serverUrl: String, token: String) = withContext(Dispatchers.IO) {
        val request = Request.Builder().url("${serverUrl.trimEnd('/')}/api/v1/device/stop-sharing")
            .header("Authorization", "Bearer $token").post(ByteArray(0).toRequestBody(mediaType)).build()
        http.newCall(request).execute().use {
            if (!it.isSuccessful && it.code != 401) throw responseException(it.code, "", null)
        }
    }

    suspend fun health(serverUrl: String) = withContext(Dispatchers.IO) {
        val request = Request.Builder().url("${serverUrl.trimEnd('/')}/api/health").get().build()
        http.newCall(request).execute().use { response ->
            val text = response.body.string()
            if (!response.isSuccessful) throw responseException(response.code, text, response.header("Retry-After"))
            if (!text.contains("\"status\":\"ok\"") && !text.contains("\"status\": \"ok\"")) {
                throw ApiHttpException(502, "服务器健康检查返回异常", null)
            }
        }
    }

    suspend fun enroll(serverUrl: String, body: EnrollBody): EnrollResponse = withContext(Dispatchers.IO) {
        val request = Request.Builder()
            .url("${serverUrl.trimEnd('/')}/api/v1/device/enroll")
            .post(json.encodeToString(EnrollBody.serializer(), body).toRequestBody(mediaType))
            .build()
        http.newCall(request).execute().use { response ->
            val text = response.body.string()
            if (!response.isSuccessful) throw responseException(response.code, text, response.header("Retry-After"))
            json.decodeFromString(EnrollResponse.serializer(), text)
        }
    }

    suspend fun upload(serverUrl: String, token: String, points: List<PendingLocation>): BatchResponse = withContext(Dispatchers.IO) {
        val payload = LocationBatch(
            points = points.map {
                LocationPayload(
                    coordinateSystem = it.coordinateSystem,
                    sequence = it.sequence,
                    capturedAt = Instant.ofEpochMilli(it.capturedAtEpochMs).toString(),
                    latitude = it.latitude,
                    longitude = it.longitude,
                    accuracyM = it.accuracyM,
                    speedMps = it.speedMps,
                    batteryPct = it.batteryPct,
                    isCharging = it.isCharging,
                    isMock = it.isMock,
                    locateRequestId = it.locateRequestId,
                )
            },
            appVersion = com.familywatch.elderlocation.BuildConfig.VERSION_NAME,
        )
        val request = Request.Builder()
            .url("${serverUrl.trimEnd('/')}/api/v1/device/locations:batch")
            .header("Authorization", "Bearer $token")
            .post(json.encodeToString(LocationBatch.serializer(), payload).toRequestBody(mediaType))
            .build()
        http.newCall(request).execute().use { response ->
            val text = response.body.string()
            if (!response.isSuccessful) throw responseException(response.code, text, response.header("Retry-After"))
            json.decodeFromString(BatchResponse.serializer(), text)
        }
    }

    suspend fun config(serverUrl: String, token: String): DeviceConfigResponse = withContext(Dispatchers.IO) {
        val request = Request.Builder()
            .url("${serverUrl.trimEnd('/')}/api/v1/device/config")
            .header("Authorization", "Bearer $token")
            .get()
            .build()
        http.newCall(request).execute().use { response ->
            val text = response.body.string()
            if (!response.isSuccessful) throw responseException(response.code, text, response.header("Retry-After"))
            json.decodeFromString(DeviceConfigResponse.serializer(), text)
        }
    }

    suspend fun waitForCommand(
        serverUrl: String,
        token: String,
        body: DeviceCommandWaitBody,
    ): LocateCommand? = withContext(Dispatchers.IO) {
        val request = Request.Builder()
            .url("${serverUrl.trimEnd('/')}/api/v1/device/commands:wait?timeoutSeconds=25")
            .header("Authorization", "Bearer $token")
            .post(json.encodeToString(DeviceCommandWaitBody.serializer(), body).toRequestBody(mediaType))
            .build()
        http.newCall(request).execute().use { response ->
            if (response.code == 204) return@withContext null
            val text = response.body.string()
            if (!response.isSuccessful) throw responseException(response.code, text, response.header("Retry-After"))
            json.decodeFromString(LocateCommand.serializer(), text)
        }
    }

    suspend fun acknowledgeLocate(serverUrl: String, token: String, requestId: String) =
        postLocateStatus(serverUrl, token, requestId, "ack", null)

    suspend fun failLocate(serverUrl: String, token: String, requestId: String, failureCode: String) =
        postLocateStatus(serverUrl, token, requestId, "fail", failureCode)

    private suspend fun postLocateStatus(
        serverUrl: String,
        token: String,
        requestId: String,
        action: String,
        failureCode: String?,
    ) = withContext(Dispatchers.IO) {
        val body = failureCode?.let {
            json.encodeToString(LocateFailureBody.serializer(), LocateFailureBody(it)).toRequestBody(mediaType)
        } ?: ByteArray(0).toRequestBody(mediaType)
        val request = Request.Builder()
            .url("${serverUrl.trimEnd('/')}/api/v1/device/locate-requests/$requestId/$action")
            .header("Authorization", "Bearer $token")
            .post(body)
            .build()
        http.newCall(request).execute().use { response ->
            val text = response.body.string()
            if (!response.isSuccessful) throw responseException(response.code, text, response.header("Retry-After"))
        }
    }

    private fun responseException(code: Int, text: String, retryAfter: String?): ApiHttpException {
        val detail = runCatching { json.decodeFromString(ApiErrorBody.serializer(), text).detail }.getOrNull()
        return ApiHttpException(code, detail, retryAfter?.toLongOrNull())
    }
}
