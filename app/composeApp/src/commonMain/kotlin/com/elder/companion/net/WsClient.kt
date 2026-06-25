package com.elder.companion.net

import io.ktor.client.HttpClient
import io.ktor.client.plugins.websocket.DefaultClientWebSocketSession
import io.ktor.client.plugins.websocket.webSocketSession
import io.ktor.websocket.Frame
import io.ktor.websocket.close
import io.ktor.websocket.readBytes
import io.ktor.websocket.readText
import kotlinx.coroutines.channels.ClosedReceiveChannelException
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.flow

/**
 * 适老端 WebSocket 客户端：连后端网关 /ws/elder/{elderId}。
 *
 * 帧约定（与 backend/server.py 一致）：
 *  - 上行二进制 = PCM 16k 音频；上行文本 = 控制 JSON（hangup）
 *  - 下行二进制 = TTS PCM 24k；下行文本 = 控制 JSON（barge_in/text/status）
 *
 * 客户端永不直连火山、不接触 API Key（PRD 安全硬边界）。
 */
class WsClient(private val httpClient: HttpClient) {

    private var session: DefaultClientWebSocketSession? = null

    /** 下行帧：二进制(音频) 或 文本(控制 JSON)。 */
    sealed interface Incoming {
        data class Audio(val pcm: ByteArray) : Incoming
        data class Control(val json: String) : Incoming
    }

    /**
     * 建立连接并返回下行帧流。流结束（正常关闭或异常）即代表会话终止。
     * @param wsUrl 形如 wss://host/ws/elder/elder-001
     */
    fun connect(wsUrl: String): Flow<Incoming> = flow {
        val s = httpClient.webSocketSession(urlString = wsUrl)
        session = s
        try {
            for (frame in s.incoming) {
                when (frame) {
                    is Frame.Binary -> emit(Incoming.Audio(frame.readBytes()))
                    is Frame.Text -> emit(Incoming.Control(frame.readText()))
                    else -> Unit
                }
            }
        } catch (_: ClosedReceiveChannelException) {
            // 对端正常关闭，流自然结束
        } finally {
            session = null
        }
    }

    /** 上行 PCM 音频帧。会话未建立时静默忽略。 */
    suspend fun sendAudio(pcm: ByteArray) {
        runCatching { session?.send(Frame.Binary(true, pcm)) }
    }

    /** 上行文本控制帧（如 hangup）。 */
    suspend fun sendText(text: String) {
        runCatching { session?.send(Frame.Text(text)) }
    }

    /** 主动关闭连接。 */
    suspend fun close() {
        runCatching { session?.close() }
        session = null
    }
}
