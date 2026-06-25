package com.elder.companion.audio

import android.media.AudioAttributes
import android.media.AudioFormat
import android.media.AudioManager
import android.media.AudioTrack
import java.util.concurrent.ConcurrentLinkedQueue
import kotlin.concurrent.thread

/**
 * Android TTS 播放：AudioTrack 24k/mono/16bit，流式写入。
 *
 * clear() 必须能立即丢弃未播缓冲——这是 barge-in 停播的关键（PRD 7.3）。
 * 用 AudioTrack.flush() + 清本地队列双保险实现即时停播。
 */
actual class AudioPlayer actual constructor() {
    private companion object {
        const val SAMPLE_RATE = 24000
    }

    private var track: AudioTrack? = null
    private val queue = ConcurrentLinkedQueue<ByteArray>()
    @Volatile private var writerRunning = false
    @Volatile private var playing = false

    actual val isPlaying: Boolean get() = playing

    actual fun prepare() {
        if (track != null) return
        val minBuf = AudioTrack.getMinBufferSize(
            SAMPLE_RATE,
            AudioFormat.CHANNEL_OUT_MONO,
            AudioFormat.ENCODING_PCM_16BIT,
        ).coerceAtLeast(SAMPLE_RATE) // ≥0.5s 缓冲

        track = AudioTrack.Builder()
            .setAudioAttributes(
                AudioAttributes.Builder()
                    .setUsage(AudioAttributes.USAGE_VOICE_COMMUNICATION)
                    .setContentType(AudioAttributes.CONTENT_TYPE_SPEECH)
                    .build()
            )
            .setAudioFormat(
                AudioFormat.Builder()
                    .setSampleRate(SAMPLE_RATE)
                    .setEncoding(AudioFormat.ENCODING_PCM_16BIT)
                    .setChannelMask(AudioFormat.CHANNEL_OUT_MONO)
                    .build()
            )
            .setBufferSizeInBytes(minBuf)
            .setTransferMode(AudioTrack.MODE_STREAM)
            .build()
            .also { it.play() }

        writerRunning = true
        thread(name = "tts-writer", isDaemon = true) {
            val t = track ?: return@thread
            while (writerRunning) {
                val chunk = queue.poll()
                if (chunk == null) {
                    playing = false
                    Thread.sleep(5)
                    continue
                }
                playing = true
                var off = 0
                while (off < chunk.size && writerRunning) {
                    val written = t.write(chunk, off, chunk.size - off)
                    if (written <= 0) break
                    off += written
                }
            }
        }
    }

    actual fun enqueue(pcm: ByteArray) {
        if (pcm.isNotEmpty()) queue.add(pcm)
    }

    actual fun clear() {
        queue.clear()
        runCatching { track?.pause(); track?.flush(); track?.play() }
        playing = false
    }

    actual fun release() {
        writerRunning = false
        queue.clear()
        runCatching { track?.stop(); track?.release() }
        track = null
        playing = false
    }
}
