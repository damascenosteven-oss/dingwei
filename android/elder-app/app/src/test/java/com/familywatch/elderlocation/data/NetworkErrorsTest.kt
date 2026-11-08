package com.familywatch.elderlocation.data

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import java.net.SocketException
import java.net.SocketTimeoutException
import java.net.UnknownHostException
import javax.net.ssl.SSLHandshakeException

class NetworkErrorsTest {
    @Test fun `maps offline and dns failures to actionable Chinese messages`() {
        assertTrue(networkErrorMessage(NoNetworkException()).contains("没有可用网络"))
        assertTrue(networkErrorMessage(UnknownHostException()).contains("解析服务器域名"))
    }

    @Test fun `maps transport failures without leaking raw English`() {
        assertTrue(networkErrorMessage(SocketTimeoutException()).contains("连接服务器超时"))
        assertTrue(networkErrorMessage(SSLHandshakeException("bad certificate")).contains("HTTPS"))
        val reset = networkErrorMessage(SocketException("Connection reset"))
        assertTrue(reset.contains("连接在到达服务器前被中断"))
        assertTrue(!reset.contains("Connection reset"))
    }

    @Test fun `maps API status and retry after`() {
        assertEquals("绑定码无效或已过期", networkErrorMessage(ApiHttpException(400, "绑定码无效或已过期", null)))
        assertTrue(networkErrorMessage(ApiHttpException(429, null, 32)).contains("32 秒"))
        assertTrue(networkErrorMessage(ApiHttpException(503, null, null)).contains("服务器暂时不可用"))
    }
}
