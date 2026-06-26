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
import kotlinx.coroutines.delay
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
    private companion object {
        const val MAX_RECONNECT = 5            // 意外断开最多自动重连次数
        const val BACKOFF_BASE_MS = 1000L      // 退避基数：1s、2s、4s…
        const val BACKOFF_CAP_MS = 8000L       // 退避上限，避免老人久等
    }

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
    private var userHangup = false   // 区分主动挂断（不重连）与意外断开（重连）

    // 音频门闩（precise stop chain）：打断后关闸，丢弃此刻仍在网络在途的"旧轮 TTS 残尾"，
    // 直到收到下一帧文本（对话向前推进=新一轮回复开始）才重新开闸。
    // 关键修复点：开闸信号从不可靠的"音频帧到达"改为可靠的"文本帧到达"——
    // 否则旧轮残尾音频到达时会被误当作新一轮而继续播放并错误重置打断标志。
    private var acceptingAudio = true

    /** 开始通话。 */
    fun start() {
        if (_ui.value.state == CallState.LIVE || _ui.value.state == CallState.CONNECTING) return
        userHangup = false
        _ui.value = CallUiState(CallState.CONNECTING, hint = "正在连接…")
        player.prepare()
        recvJob = scope.launch { runConnectionLoop() }
        startCapture()
    }

    /**
     * 连接生命周期循环：首连 + 意外断开后的退避自动重连。
     * 借鉴 X-OmniClaw "failures converge and execution continues"——网络抖动不让老人手动重拨。
     * 主动挂断(userHangup)或重试耗尽才真正结束。
     */
    private suspend fun runConnectionLoop() {
        var attempt = 0
        while (!userHangup) {
            val gracefully = try {
                ws.connect(wsUrl()).collect { handleIncoming(it) }
                true  // 流正常结束 = 对端关闭
            } catch (e: Exception) {
                false // 连接异常断开
            }
            if (userHangup) break

            attempt = if (gracefully) attempt else attempt + 1
            // 正常结束（对端主动关闭，如会话 ended）不重连；仅异常断开才重连
            if (gracefully || attempt > MAX_RECONNECT) break

            val backoff = (BACKOFF_BASE_MS shl (attempt - 1)).coerceAtMost(BACKOFF_CAP_MS)
            _ui.value = _ui.value.copy(state = CallState.RECONNECTING, hint = "网络不稳，正在重新连接…")
            player.clear()
            delay(backoff)
            if (userHangup) break
            _ui.value = _ui.value.copy(hint = "正在重新连接…（第 $attempt 次）")
        }
        cleanup()
    }

    /** 挂断：先发 hangup 信令，再清理本地。 */
    fun hangup() {
        userHangup = true
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
                    interrupt() // 本地 VAD 抢跑打断
                }
            }
        }
    }

    /** 统一打断：停播 + 关音频门闩丢弃旧轮在途残尾。本地 VAD 与服务端 barge_in 共用。 */
    private fun interrupt() {
        player.clear()
        bargedThisTurn = true
        acceptingAudio = false
    }

    private suspend fun handleIncoming(incoming: WsClient.Incoming) {
        when (incoming) {
            is WsClient.Incoming.Audio -> {
                if (acceptingAudio) player.enqueue(incoming.pcm) // 关闸期间丢弃旧轮残尾
            }
            is WsClient.Incoming.Control -> applyControl(ControlFrame.parse(incoming.json))
        }
    }

    private fun applyControl(frame: ServerFrame) {
        when (frame) {
            is ServerFrame.BargeIn -> interrupt() // 服务端兜底：本地未触发时由此停播
            is ServerFrame.Text -> {
                // 文本推进 = 新一轮回复开始：重新开闸并允许再次本地打断。
                // 以可靠的文本帧而非易乱序的音频帧作为轮次边界。
                acceptingAudio = true
                bargedThisTurn = false
                _ui.value = _ui.value.copy(
                    subtitleRole = frame.role,
                    subtitleText = frame.text,
                )
            }
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
        acceptingAudio = true
        // 终态：主动挂断/对端正常结束 -> IDLE；重试耗尽的意外断开 -> ERROR。
        val st = _ui.value.state
        if (st == CallState.RECONNECTING) {
            _ui.value = CallUiState(CallState.ERROR, hint = "网络断开了，请稍后再试")
        } else if (st == CallState.LIVE || st == CallState.CONNECTING) {
            _ui.value = CallUiState(CallState.IDLE, hint = "已挂断")
        }
    }

    /** 释放全部资源（页面销毁时调用）。 */
    fun dispose() {
        userHangup = true
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
