package com.elder.companion.ui

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.elder.companion.session.CallState
import com.elder.companion.session.CallUiState

/**
 * 适老化通话主界面：一个超大圆形按钮 + 大字状态 + 字幕兜底。
 *
 * 设计对齐 PRD 6.1 适老化：打开即连、语音为主、大字兜底；视觉只做状态指示。
 * 不堆砌信息——老人只需看懂"现在能不能说话"和"要不要点这个大按钮"。
 */
@Composable
fun CallScreen(
    uiState: CallUiState,
    onToggle: () -> Unit,
) {
    val live = uiState.state == CallState.LIVE || uiState.state == CallState.CONNECTING

    Box(
        modifier = Modifier
            .fillMaxSize()
            .background(MaterialTheme.colorScheme.background)
            .padding(24.dp),
        contentAlignment = Alignment.Center,
    ) {
        Column(
            horizontalAlignment = Alignment.CenterHorizontally,
            verticalArrangement = Arrangement.spacedBy(40.dp),
        ) {
            Text(
                text = uiState.hint,
                fontSize = 30.sp,
                textAlign = TextAlign.Center,
                color = MaterialTheme.colorScheme.onBackground,
            )

            Button(
                onClick = onToggle,
                shape = CircleShape,
                modifier = Modifier.size(220.dp),
                colors = ButtonDefaults.buttonColors(
                    containerColor = if (live) Color(0xFFD32F2F) else Color(0xFF2E7D32),
                ),
            ) {
                Text(
                    text = if (live) "挂断" else "开始\n说话",
                    fontSize = 40.sp,
                    textAlign = TextAlign.Center,
                    color = Color.White,
                )
            }

            Subtitle(uiState)
        }
    }
}

@Composable
private fun Subtitle(uiState: CallUiState) {
    if (uiState.subtitleText.isBlank()) return
    val who = if (uiState.subtitleRole == "user") "我" else "TA"
    Text(
        text = "$who：${uiState.subtitleText}",
        fontSize = 24.sp,
        textAlign = TextAlign.Center,
        color = MaterialTheme.colorScheme.onBackground.copy(alpha = 0.75f),
        modifier = Modifier.fillMaxWidth(),
    )
}
