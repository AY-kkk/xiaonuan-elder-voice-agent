package com.elder.companion.session

import com.elder.companion.audio.AudioCapture
import com.elder.companion.audio.AudioPlayer
import com.elder.companion.audio.LocalVad
import com.elder.companion.net.WsClient
import com.elder.companion.net.createHttpClient
import com.elder.companion.protocol.ControlFrame
import com.elder.companion.protocol.ServerFrame
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch

/**
 * 通话编排器：串起 WS 收发、麦克风采集、TTS 播放、本地 VAD 打断。
 *
 * 全双工链路（对齐 web/elder/app.js）：
 *  - 上行：AudioCapture 帧 -> WsClient.sendAudio；同时本地 VAD 判打断
 *  - 下行：WsClient 二进制 -> AudioPlayer.enqueue；文本 -> 状态/字幕/barge_in
 *  - 打断：本地 VAD 抢跑 clear()；服务端 barge_in 兜底 clear()
 *
 * 不持有任何记忆/密钥——记忆在后端，客户端只管音频管线与 UI 状态。
 */
class CallController(
    private val serverHost: String,
    private val elderId: String = "elder-001",
) {
    private val scope = CoroutineScope(SupervisorJob())
    private val http = createHttpClient()
    private val ws = WsClient(http)
    private val capture = AudioCapture()
    private val player = AudioPlayer()
    private val vad = LocalVad()

    private val _ui = MutableStateFlow(CallUiState())
    val ui: StateFlow<CallUiState> = _ui.asStateFlow()

    private var recvJob: Job? = null
    private var sendJob: Job? = null
    private var bargedThisTurn = false

    /** 开始通话。 */
    fun start() {
        if (_ui.value.state == CallState.LIVE || _ui.value.state == CallState.CONNECTING) return
        _ui.value = CallUiState(CallState.CONNECTING, hint = "正在连接…")
        player.prepare()

        recvJob = scope.launch {
            try {
                ws.connect(wsUrl()).collect { handleIncoming(it) }
            } catch (e: Exception) {
                _ui.value = CallUiState(CallState.ERROR, hint = "连接出错了，请再试一次")
            } finally {
                cleanup()
            }
        }
        startCapture()
    }

    /** 挂断：先发 hangup 信令，再清理本地。 */
    fun hangup() {
        scope.launch {
            ws.sendText(ControlFrame.hangup())
            ws.close()
            cleanup()
        }
    }

    private fun startCapture() {
        sendJob = scope.launch {
            capture.start().collect { frame ->
                ws.sendAudio(frame.pcm)
                if (vad.onFrame(frame.rms, player.isPlaying, bargedThisTurn)) {
                    player.clear()
                    bargedThisTurn = true
                }
            }
        }
    }

    private suspend fun handleIncoming(incoming: WsClient.Incoming) {
        when (incoming) {
            is WsClient.Incoming.Audio -> {
                player.enqueue(incoming.pcm)
                bargedThisTurn = false // 新一轮回复开始，允许再次本地打断
            }
            is WsClient.Incoming.Control -> applyControl(ControlFrame.parse(incoming.json))
        }
    }

    private fun applyControl(frame: ServerFrame) {
        when (frame) {
            is ServerFrame.BargeIn -> {
                player.clear() // 服务端兜底：本地未触发时由此停播
                bargedThisTurn = true
            }
            is ServerFrame.Text -> _ui.value = _ui.value.copy(
                subtitleRole = frame.role,
                subtitleText = frame.text,
            )
            is ServerFrame.Status -> applyStatus(frame.status)
            ServerFrame.Unknown -> Unit
        }
    }

    private fun applyStatus(status: String) {
        when (status) {
            "connected" -> _ui.value = _ui.value.copy(state = CallState.LIVE, hint = "接通啦，您说话吧")
            "ended" -> _ui.value = CallUiState(CallState.IDLE, hint = "已挂断")
            "error" -> _ui.value = CallUiState(CallState.ERROR, hint = "出了点小问题，请再试一次")
        }
    }

    private fun cleanup() {
        sendJob?.cancel(); sendJob = null
        capture.stop()
        player.clear()
        vad.reset()
        bargedThisTurn = false
        if (_ui.value.state == CallState.LIVE || _ui.value.state == CallState.CONNECTING) {
            _ui.value = CallUiState(CallState.IDLE, hint = "已挂断")
        }
    }

    /** 释放全部资源（页面销毁时调用）。 */
    fun dispose() {
        recvJob?.cancel()
        cleanup()
        player.release()
        scope.cancel()
        http.close()
    }

    private fun wsUrl(): String {
        val scheme = if (serverHost.startsWith("localhost") || serverHost.startsWith("10.") ||
            serverHost.startsWith("192.168.") || serverHost.startsWith("127.")
        ) "ws" else "wss"
        return "$scheme://$serverHost/ws/elder/$elderId"
    }
}
