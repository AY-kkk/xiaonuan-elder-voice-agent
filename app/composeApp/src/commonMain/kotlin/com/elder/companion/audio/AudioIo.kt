package com.elder.companion.audio

import kotlinx.coroutines.flow.Flow

/**
 * 麦克风采集（平台相关）。
 *
 * 契约（与后端约定）：
 *  - 输出 PCM：16000Hz / 单声道 / 16bit / 小端，约 20ms 一帧。
 *  - 平台实现必须开启系统级回声消除（AEC），避免 Agent 自己的 TTS 被回采
 *    误触发本地打断（见 PRD 7.3 / web/elder/app.js AEC 说明）。
 *
 * expect/actual 边界只切在音频 I/O：Android=AudioRecord，iOS=AVAudioEngine。
 */
expect class AudioCapture() {
    /** 启动采集，返回 PCM 帧流。每帧已是 16k/mono/16bit-LE 字节。 */
    fun start(): Flow<AudioFrame>

    /** 停止采集并释放底层资源。可重复调用。 */
    fun stop()
}

/**
 * 扬声器播放（平台相关）。
 *
 * 契约：入参 PCM 为 24000Hz / 单声道 / 16bit / 小端（火山下行 TTS 采样率）。
 * 必须支持 [clear] 立即丢弃未播缓冲——这是 barge-in 打断停播的关键。
 */
expect class AudioPlayer() {
    /** 准备播放管线（申请音频焦点 / 会话）。 */
    fun prepare()

    /** 追加一段下行 PCM 到播放队列。 */
    fun enqueue(pcm: ByteArray)

    /** 立即清空未播缓冲并停播（打断时调用）。 */
    fun clear()

    /** 当前是否正在出声——本地 VAD 仅在出声时判定打断。 */
    val isPlaying: Boolean

    /** 释放底层资源。 */
    fun release()
}

/**
 * 一帧采集音频。
 * @param pcm 16k/mono/16bit-LE 原始字节。
 * @param rms 该帧均方根能量（0~1），供本地 VAD 判定开口。
 */
data class AudioFrame(val pcm: ByteArray, val rms: Float) {
    override fun equals(other: Any?): Boolean =
        this === other || (other is AudioFrame && pcm.contentEquals(other.pcm) && rms == other.rms)

    override fun hashCode(): Int = pcm.contentHashCode() * 31 + rms.hashCode()
}
