package com.elder.companion.audio

import kotlinx.cinterop.ExperimentalForeignApi
import kotlinx.cinterop.get
import platform.AVFAudio.AVAudioCommonFormat
import platform.AVFAudio.AVAudioEngine
import platform.AVFAudio.AVAudioFormat
import platform.AVFAudio.AVAudioPCMBuffer
import platform.AVFAudio.AVAudioPlayerNode

/**
 * iOS TTS 播放：AVAudioEngine + AVAudioPlayerNode，24k/mono/16bit 流式调度。
 *
 * clear() 用 playerNode.stop() 立即丢弃已调度但未播的 buffer——barge-in 停播关键。
 */
@OptIn(ExperimentalForeignApi::class)
actual class AudioPlayer actual constructor() {
    private companion object {
        const val SAMPLE_RATE = 24000.0
    }

    private val engine = AVAudioEngine()
    private val node = AVAudioPlayerNode()
    private var format: AVAudioFormat? = null
    private var scheduled = 0

    actual val isPlaying: Boolean get() = node.playing && scheduled > 0

    actual fun prepare() {
        if (format != null) return
        val fmt = AVAudioFormat(
            commonFormat = AVAudioCommonFormat.AVAudioPCMFormatInt16,
            sampleRate = SAMPLE_RATE,
            channels = 1u,
            interleaved = true,
        ) ?: return
        format = fmt
        engine.attachNode(node)
        engine.connect(node, engine.mainMixerNode, fmt)
        engine.prepare()
        runCatching { engine.startAndReturnError(null) }
        node.play()
    }

    actual fun enqueue(pcm: ByteArray) {
        val fmt = format ?: return
        if (pcm.isEmpty()) return
        val frameCount = (pcm.size / 2).toUInt()
        val buffer = AVAudioPCMBuffer(pCMFormat = fmt, frameCapacity = frameCount) ?: return
        buffer.frameLength = frameCount

        val channelData = buffer.int16ChannelData ?: return
        val ch0 = channelData[0] ?: return
        for (i in 0 until pcm.size / 2) {
            val lo = pcm[i * 2].toInt() and 0xFF
            val hi = pcm[i * 2 + 1].toInt()
            ch0[i] = ((hi shl 8) or lo).toShort()
        }

        scheduled++
        node.scheduleBuffer(buffer, completionHandler = { scheduled-- })
        if (!node.playing) node.play()
    }

    actual fun clear() {
        runCatching { node.stop() }
        scheduled = 0
        if (format != null) node.play()
    }

    actual fun release() {
        runCatching { node.stop() }
        runCatching { engine.stop() }
        scheduled = 0
        format = null
    }
}
