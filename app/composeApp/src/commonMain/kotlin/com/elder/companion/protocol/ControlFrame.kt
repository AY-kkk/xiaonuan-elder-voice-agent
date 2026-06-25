package com.elder.companion.protocol

import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import kotlinx.serialization.json.put

/**
 * 适老端 <-> 后端的文本控制帧编解码。
 *
 * 与后端 backend/session/manager.py 的约定严格一致：
 *  - 上行：{"type":"hangup"}
 *  - 下行：{"type":"barge_in"}
 *          {"type":"text","role":"user"|"assistant","text":"..."}
 *          {"type":"status","status":"connected"|"ended"|"error","detail":"..."}
 *
 * 二进制帧（PCM 音频）不走这里，由 WsClient 直接收发字节。
 */
object ControlFrame {
    private val json = Json { ignoreUnknownKeys = true; encodeDefaults = false }

    /** 上行挂断信令。 */
    fun hangup(): String = buildJsonObject { put("type", "hangup") }.toString()

    /** 解析下行文本帧；无法识别时返回 [ServerFrame.Unknown]，调用方安全忽略。 */
    fun parse(raw: String): ServerFrame {
        val obj: JsonObject = try {
            json.parseToJsonElement(raw).jsonObject
        } catch (_: Exception) {
            return ServerFrame.Unknown
        }
        return when (obj.str("type")) {
            "barge_in" -> ServerFrame.BargeIn
            "text" -> ServerFrame.Text(
                role = obj.str("role") ?: "assistant",
                text = obj.str("text") ?: "",
            )
            "status" -> ServerFrame.Status(
                status = obj.str("status") ?: "",
                detail = obj.str("detail") ?: "",
            )
            else -> ServerFrame.Unknown
        }
    }

    private fun JsonObject.str(key: String): String? =
        this[key]?.jsonPrimitive?.content
}

/** 下行文本帧的结构化表示。 */
sealed interface ServerFrame {
    data object BargeIn : ServerFrame
    data class Text(val role: String, val text: String) : ServerFrame
    data class Status(val status: String, val detail: String) : ServerFrame
    data object Unknown : ServerFrame
}
