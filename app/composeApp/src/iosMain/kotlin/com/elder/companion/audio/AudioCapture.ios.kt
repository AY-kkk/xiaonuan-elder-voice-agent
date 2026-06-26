package com.elder.companion.audio

import kotlinx.cinterop.ExperimentalForeignApi
import kotlinx.cinterop.get
import kotlinx.cinterop.pointed
import kotlinx.cinterop.value
import kotlinx.coroutines.channels.BufferOverflow
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.MutableSharedFlow
import platform.AVFAudio.AVAudioConverter
import platform.AVFAudio.AVAudioConverterInputStatus_HaveData
import platform.AVFAudio.AVAudioConverterInputStatus_NoDataNow
import platform.AVFAudio.AVAudioEngine
import platform.AVFAudio.AVAudioFormat
import platform.AVFAudio.AVAudioPCMBuffer
import platform.AVFAudio.AVAudioPCMFormatInt16
import platform.AVFAudio.AVAudioSession
import platform.AVFAudio.AVAudioSessionCategoryPlayAndRecord
import platform.AVFAudio.AVAudioSessionCategoryOptionDefaultToSpeaker
import platform.AVFAudio.AVAudioSessionModeVoiceChat
import platform.AVFAudio.setActive
import kotlin.math.sqrt

/**
 * iOS 麦克风采集：AVAudioEngine + voiceProcessing(系统 AEC) -> 16k/mono/16bit。
 *
 * AEC 通过 AVAudioSession mode=voiceChat + inputNode.setVoiceProcessingEnabled 启用，
 * 防止 Agent TTS 自播被回采误触发本地打断（PRD 7.3，对齐 X-OmniClaw AEC 思路）。
 *
 * 硬件 tap 通常是 48k float，用 AVAudioConverter 重采样到 16k int16 后回调。
 */
@OptIn(ExperimentalForeignApi::class)
actual class AudioCapture actual constructor() {
    private companion object {
        const val TARGET_RATE = 16000.0
    }

    private val engine = AVAudioEngine()
    private var converter: AVAudioConverter? = null

    private val frames = MutableSharedFlow<AudioFrame>(
        extraBufferCapacity = 64,
        onBufferOverflow = BufferOverflow.DROP_OLDEST,
    )

    actual fun start(): Flow<AudioFrame> {
        configureSession()

        val input = engine.inputNode
        runCatching { input.setVoiceProcessingEnabled(true, null) } // 系统 AEC

        val hwFormat = input.outputFormatForBus(0u)
        val targetFormat = AVAudioFormat(
            commonFormat = AVAudioPCMFormatInt16,
            sampleRate = TARGET_RATE,
            channels = 1u,
            interleaved = true,
        )
        if (targetFormat == null) return frames
        converter = AVAudioConverter(fromFormat = hwFormat, toFormat = targetFormat)

        input.installTapOnBus(0u, bufferSize = 1024u, format = hwFormat) { buffer, _ ->
            if (buffer != null) convertAndEmit(buffer, targetFormat)
        }

        engine.prepare()
        runCatching { engine.startAndReturnError(null) }
        return frames
    }

    actual fun stop() {
        runCatching { engine.inputNode.removeTapOnBus(0u) }
        runCatching { engine.stop() }
        converter = null
    }

    private fun configureSession() {
        val session = AVAudioSession.sharedInstance()
        runCatching {
            session.setCategory(
                AVAudioSessionCategoryPlayAndRecord,
                mode = AVAudioSessionModeVoiceChat,
                options = AVAudioSessionCategoryOptionDefaultToSpeaker,
                error = null,
            )
            session.setActive(true, null)
        }
    }

    private fun convertAndEmit(src: AVAudioPCMBuffer, targetFormat: AVAudioFormat) {
        val conv = converter ?: return
        val ratio = TARGET_RATE / src.format.sampleRate
        val outCapacity = ((src.frameLength.toDouble() * ratio) + 1).toUInt()
        val outBuffer = AVAudioPCMBuffer(pCMFormat = targetFormat, frameCapacity = outCapacity) ?: return

        var supplied = false
        conv.convertToBuffer(outBuffer, error = null) { _, statusPtr ->
            if (supplied) {
                statusPtr?.pointed?.value = AVAudioConverterInputStatus_NoDataNow
                null
            } else {
                supplied = true
                statusPtr?.pointed?.value = AVAudioConverterInputStatus_HaveData
                src
            }
        }

        val n = outBuffer.frameLength.toInt()
        if (n <= 0) return
        val channelData = outBuffer.int16ChannelData ?: return
        val ch0 = channelData[0] ?: return

        val bytes = ByteArray(n * 2)
        var sum = 0.0
        for (i in 0 until n) {
            val sample = ch0[i]
            bytes[i * 2] = (sample.toInt() and 0xFF).toByte()
            bytes[i * 2 + 1] = ((sample.toInt() shr 8) and 0xFF).toByte()
            val v = sample / 32768.0
            sum += v * v
        }
        val rms = sqrt(sum / n).toFloat()
        frames.tryEmit(AudioFrame(bytes, rms))
    }
}
