package com.elder.companion.audio

import android.media.AudioFormat
import android.media.AudioRecord
import android.media.MediaRecorder
import android.media.audiofx.AcousticEchoCanceler
import android.media.audiofx.NoiseSuppressor
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.flow
import kotlinx.coroutines.flow.flowOn
import kotlin.math.sqrt

/**
 * Android 麦克风采集：AudioRecord 16k/mono/16bit + 系统级回声消除。
 *
 * AEC 是借鉴 X-OmniClaw 的关键点——防止 Agent 自己的 TTS 被回采误触发本地打断
 * （PRD 7.3）。VOICE_COMMUNICATION 源会启用平台 AEC/AGC，再叠加显式 AEC/NS effect。
 */
actual class AudioCapture actual constructor() {
    private companion object {
        const val SAMPLE_RATE = 16000
        const val FRAME_SAMPLES = 320          // 20ms @ 16k
        const val FRAME_BYTES = FRAME_SAMPLES * 2
    }

    @Volatile private var record: AudioRecord? = null
    private var aec: AcousticEchoCanceler? = null
    private var ns: NoiseSuppressor? = null

    @Volatile private var running = false

    actual fun start(): Flow<AudioFrame> = flow {
        val minBuf = AudioRecord.getMinBufferSize(
            SAMPLE_RATE,
            AudioFormat.CHANNEL_IN_MONO,
            AudioFormat.ENCODING_PCM_16BIT,
        ).coerceAtLeast(FRAME_BYTES * 4)

        val rec = AudioRecord(
            MediaRecorder.AudioSource.VOICE_COMMUNICATION, // 启用平台 AEC/AGC
            SAMPLE_RATE,
            AudioFormat.CHANNEL_IN_MONO,
            AudioFormat.ENCODING_PCM_16BIT,
            minBuf,
        )
        if (rec.state != AudioRecord.STATE_INITIALIZED) {
            rec.release()
            throw IllegalStateException("AudioRecord 初始化失败（检查麦克风权限）")
        }
        enableEffects(rec.audioSessionId)
        record = rec
        running = true

        try {
            rec.startRecording()
            val buf = ByteArray(FRAME_BYTES)
            while (running) {
                val n = rec.read(buf, 0, FRAME_BYTES)
                if (n <= 0) continue
                val frame = if (n == FRAME_BYTES) buf.copyOf() else buf.copyOf(n)
                emit(AudioFrame(frame, rms(frame, n)))
            }
        } finally {
            releaseInternal()
        }
    }.flowOn(Dispatchers.IO)

    actual fun stop() {
        running = false
    }

    private fun enableEffects(sessionId: Int) {
        runCatching {
            if (AcousticEchoCanceler.isAvailable()) {
                aec = AcousticEchoCanceler.create(sessionId)?.apply { enabled = true }
            }
            if (NoiseSuppressor.isAvailable()) {
                ns = NoiseSuppressor.create(sessionId)?.apply { enabled = true }
            }
        }
    }

    private fun releaseInternal() {
        runCatching { record?.stop() }
        runCatching { aec?.release() }
        runCatching { ns?.release() }
        runCatching { record?.release() }
        record = null; aec = null; ns = null
    }

    /** 16bit-LE 字节序列的归一化 RMS（0~1），供本地 VAD 判定开口。 */
    private fun rms(bytes: ByteArray, len: Int): Float {
        var sum = 0.0
        var i = 0
        val count = len / 2
        while (i < len - 1) {
            val s = (bytes[i].toInt() and 0xFF) or (bytes[i + 1].toInt() shl 8)
            val v = s / 32768.0
            sum += v * v
            i += 2
        }
        return if (count == 0) 0f else sqrt(sum / count).toFloat()
    }
}
