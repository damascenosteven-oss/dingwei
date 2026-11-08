package com.familywatch.elderlocation.data

import java.net.SocketException
import java.net.SocketTimeoutException
import java.net.UnknownHostException
import javax.net.ssl.SSLException

class NoNetworkException : RuntimeException("No active network")

fun networkErrorMessage(error: Throwable): String {
    val causes = generateSequence(error as Throwable?) { it.cause }.toList()
    return when {
        causes.any { it is NoNetworkException } -> "当前没有可用网络，请连接 Wi‑Fi 或打开移动数据后重试。"
        causes.any { it is UnknownHostException } -> "无法解析服务器域名，请检查网络或 DNS 设置后重试。"
        causes.any { it is SocketTimeoutException } -> "连接服务器超时，请确认网络稳定后重试。"
        causes.any { it is SSLException } -> "HTTPS 安全连接失败，请检查系统时间和服务器证书。"
        causes.any { it is SocketException && it.message?.contains("reset", ignoreCase = true) == true } ->
            "连接在到达服务器前被中断。若域名仍在备案审核中，请等待备案通过；否则请切换网络重试，并查看网页设备状态。若网页已显示绑定、手机仍停留本页，请重新生成绑定码。"
        error is ApiHttpException && error.statusCode == 400 -> error.serverDetail ?: "绑定码无效或已过期，请在网页重新生成。"
        error is ApiHttpException && error.statusCode == 401 -> "设备身份已失效，请由家属重新绑定。"
        error is ApiHttpException && error.statusCode == 429 -> {
            val wait = error.retryAfterSeconds?.let { "请等待 ${it} 秒后重试。" } ?: "请稍后再试。"
            "操作过于频繁，$wait"
        }
        error is ApiHttpException && error.statusCode >= 500 -> "服务器暂时不可用，请稍后重试。"
        error is ApiHttpException -> error.serverDetail ?: "服务器拒绝了请求（${error.statusCode}）。"
        else -> "暂时无法连接服务器，请检查网络后重试。"
    }
}
