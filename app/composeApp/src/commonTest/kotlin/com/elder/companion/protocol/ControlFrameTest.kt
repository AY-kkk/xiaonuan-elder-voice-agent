package com.elder.companion.protocol

import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertTrue
import kotlin.test.assertIs

/**
 * 控制帧契约单测（R5）：KMP 端 ControlFrame 必须与 docs/protocol.md 一致。
 *
 * 覆盖 docs/protocol.md §4 全部下行 type（barge_in/text/status）+ 未知帧降级，
 * 以及 §3 上行 hangup 编码。任一处与文档/后端漂移即失败。
 *
 * 注：本测试随 `gradle :composeApp:testDebugUnitTest` 在 CI 运行（见 native-build.yml）。
 */
class ControlFrameTest {

    @Test
    fun parse_barge_in() {
        assertIs<ServerFrame.BargeIn>(ControlFrame.parse("""{"type":"barge_in"}"""))
    }

    @Test
    fun parse_text_carries_role_and_text() {
        val frame = ControlFrame.parse("""{"type":"text","role":"assistant","text":"今天天气不错呢"}""")
        assertIs<ServerFrame.Text>(frame)
        assertEquals("assistant", frame.role)
        assertEquals("今天天气不错呢", frame.text)
    }

    @Test
    fun parse_status_carries_status_and_detail() {
        val frame = ControlFrame.parse("""{"type":"status","status":"error","detail":"connect_failed"}""")
        assertIs<ServerFrame.Status>(frame)
        assertEquals("error", frame.status)
        assertEquals("connect_failed", frame.detail)
    }

    @Test
    fun parse_unknown_type_falls_back() {
        assertIs<ServerFrame.Unknown>(ControlFrame.parse("""{"type":"future_frame"}"""))
    }

    @Test
    fun parse_malformed_json_falls_back() {
        assertIs<ServerFrame.Unknown>(ControlFrame.parse("not a json"))
    }

    @Test
    fun hangup_encodes_upstream_type() {
        assertTrue(ControlFrame.hangup().contains("\"type\":\"hangup\""))
    }
}
