package com.elder.companion.ui

import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.lightColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.remember
import androidx.compose.ui.graphics.Color
import com.elder.companion.session.CallController
import com.elder.companion.session.CallState

/**
 * 后端网关地址。真机/模拟器联调时改成局域网 IP（如 192.168.x.x:8000）。
 * 客户端只连自有后端，永不直连火山（PRD 安全硬边界）。
 */
private const val SERVER_HOST = "10.0.2.2:8000" // Android 模拟器访问宿主机的默认地址

private val ElderColors = lightColorScheme(
    background = Color(0xFFF5F5F0),
    onBackground = Color(0xFF1A1A1A),
)

@Composable
fun App() {
    val controller = remember { CallController(serverHost = SERVER_HOST) }
    val uiState by controller.ui.collectAsState()

    DisposableEffect(Unit) {
        onDispose { controller.dispose() }
    }

    MaterialTheme(colorScheme = ElderColors) {
        CallScreen(
            uiState = uiState,
            onToggle = {
                val live = uiState.state == CallState.LIVE || uiState.state == CallState.CONNECTING
                if (live) controller.hangup() else controller.start()
            },
        )
    }
}
