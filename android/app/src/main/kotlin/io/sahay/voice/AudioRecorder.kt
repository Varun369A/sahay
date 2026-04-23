package io.sahay.voice

import android.content.Context
import android.media.AudioFormat
import android.media.AudioRecord
import android.media.MediaRecorder
import dagger.hilt.android.qualifiers.ApplicationContext
import java.io.File
import java.io.FileOutputStream
import java.nio.ByteBuffer
import java.nio.ByteOrder
import javax.inject.Inject
import javax.inject.Singleton
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext

/**
 * Simple PCM 16 kHz mono WAV recorder.
 * Holds the mic only while recording — released immediately on stop.
 */
@Singleton
class AudioRecorder @Inject constructor(@ApplicationContext private val context: Context) {

    private val sampleRate = 16_000
    private val channelConfig = AudioFormat.CHANNEL_IN_MONO
    private val audioFormat = AudioFormat.ENCODING_PCM_16BIT

    @Volatile private var stopFlag = false

    /**
     * Records up to maxMillis or until stop() is called.
     * Returns the recorded WAV file (or null on error).
     */
    suspend fun record(maxMillis: Long = 15_000): File? = withContext(Dispatchers.IO) {
        val bufferSize = AudioRecord.getMinBufferSize(sampleRate, channelConfig, audioFormat)
        if (bufferSize <= 0) return@withContext null

        @Suppress("MissingPermission")
        val recorder = AudioRecord(
            MediaRecorder.AudioSource.MIC,
            sampleRate,
            channelConfig,
            audioFormat,
            bufferSize * 2,
        )

        if (recorder.state != AudioRecord.STATE_INITIALIZED) {
            recorder.release()
            return@withContext null
        }

        val outFile = File(context.cacheDir, "sahay_voice_${System.currentTimeMillis()}.wav")
        val pcm = ByteArray(bufferSize)
        val outStream = FileOutputStream(outFile)
        writeWavHeaderPlaceholder(outStream)

        var totalBytes = 0
        stopFlag = false
        recorder.startRecording()
        val deadline = System.currentTimeMillis() + maxMillis
        try {
            while (!stopFlag && System.currentTimeMillis() < deadline) {
                val read = recorder.read(pcm, 0, pcm.size)
                if (read > 0) {
                    outStream.write(pcm, 0, read)
                    totalBytes += read
                }
            }
        } finally {
            recorder.stop()
            recorder.release()
            outStream.flush()
            outStream.close()
        }

        // Patch WAV header with correct sizes.
        patchWavHeader(outFile, totalBytes, sampleRate)
        outFile
    }

    fun stop() { stopFlag = true }

    // ─── WAV helpers ─────────────────────────────────────────────────

    private fun writeWavHeaderPlaceholder(out: FileOutputStream) {
        val header = ByteArray(44)
        out.write(header)
    }

    private fun patchWavHeader(file: File, dataBytes: Int, sampleRate: Int) {
        val header = ByteBuffer.allocate(44).order(ByteOrder.LITTLE_ENDIAN)
        header.put("RIFF".toByteArray())
        header.putInt(36 + dataBytes)
        header.put("WAVE".toByteArray())
        header.put("fmt ".toByteArray())
        header.putInt(16)
        header.putShort(1)                    // PCM
        header.putShort(1)                    // mono
        header.putInt(sampleRate)
        header.putInt(sampleRate * 2)         // byte rate
        header.putShort(2)                    // block align
        header.putShort(16)                   // bits per sample
        header.put("data".toByteArray())
        header.putInt(dataBytes)

        val raf = java.io.RandomAccessFile(file, "rw")
        raf.use {
            it.seek(0)
            it.write(header.array())
        }
    }
}
