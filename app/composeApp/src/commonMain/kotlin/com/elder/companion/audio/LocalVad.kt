package com.elder.companion.audio

/**
 * 本地语音活动检测（VAD），用于即时打断。
 *
 * 逻辑复刻 web/elder/app.js：Agent 出声期间，连续多帧高能量即判老人插话，
 * 立刻本地停播，不等服务端 barge_in 往返（目标 ≤300ms）。阈值偏保守，
 * 配合系统 AEC 降低 TTS 自播误触发。
 *
 * 纯算法、无平台依赖，放 commonMain 全端共享。
 */
class LocalVad(
    private val rmsThreshold: Float = 0.04f,
    private val triggerFrames: Int = 4, // 连续 4 帧 ≈ 80ms 才算开口，过滤瞬时噪声
) {
    private var voiceFrames = 0

    /**
     * 投喂一帧的 RMS，返回是否应触发本地打断。
     *
     * @param rms 当前帧能量。
     * @param agentSpeaking Agent 是否正在出声（仅出声时才检测）。
     * @param alreadyBarged 本轮是否已打断过（避免重复 clear）。
     */
    fun onFrame(rms: Float, agentSpeaking: Boolean, alreadyBarged: Boolean): Boolean {
        if (!agentSpeaking || alreadyBarged) {
            voiceFrames = 0
            return false
        }
        if (rms >= rmsThreshold) {
            voiceFrames += 1
            if (voiceFrames >= triggerFrames) {
                voiceFrames = 0
                return true
            }
        } else {
            voiceFrames = 0
        }
        return false
    }

    fun reset() {
        voiceFrames = 0
    }
}
