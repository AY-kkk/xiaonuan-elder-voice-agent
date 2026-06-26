package com.elder.companion.session

/** 通话状态（驱动适老化 UI 的单一状态源）。 */
enum class CallState {
    IDLE,          // 未通话，显示"开始说话"
    CONNECTING,    // 正在连接
    LIVE,          // 通话中
    RECONNECTING,  // 网络抖动断开，正在自动重连（不需老人操作）
    ERROR,         // 出错
}

/** UI 可观察的通话快照。 */
data class CallUiState(
    val state: CallState = CallState.IDLE,
    val hint: String = "轻触开始说话",
    val subtitleRole: String = "",   // "user" | "assistant" | ""
    val subtitleText: String = "",
)
