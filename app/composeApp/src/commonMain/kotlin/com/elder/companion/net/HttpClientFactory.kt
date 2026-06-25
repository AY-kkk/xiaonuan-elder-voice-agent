package com.elder.companion.net

import io.ktor.client.HttpClient

/** 平台相关的 Ktor HttpClient（已装 WebSockets 插件）。Android=CIO，iOS=Darwin。 */
expect fun createHttpClient(): HttpClient
