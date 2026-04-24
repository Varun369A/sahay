package io.sahay.service

import android.app.Notification
import android.app.PendingIntent
import android.app.Service
import android.content.Intent
import android.content.pm.ServiceInfo
import android.media.AudioFormat
import android.media.AudioRecord
import android.media.MediaRecorder
import android.os.Build
import android.os.IBinder
import android.util.Log
import androidx.core.app.NotificationCompat
import dagger.hilt.android.AndroidEntryPoint
import io.sahay.App
import io.sahay.MainActivity
import io.sahay.R
import io.sahay.data.secrets.UserPreferences
import io.sahay.engine.TaskQueue
import io.sahay.engine.TaskWorker
import io.sahay.voice.AudioRecorder
import io.sahay.voice.SarvamSttClient
import io.sahay.voice.VoskModelManager
import javax.inject.Inject
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import org.json.JSONObject
import org.vosk.Recognizer

/**
 * Always-on wake-word listener using Vosk (offline, silent, no API key).
 *
 * Why not Android SpeechRecognizer:
 *   • Plays a system "listening" beep on every restart cycle (one per
 *     ~3-5s). Hard to suppress on most OEMs. Killed UX.
 *   • Partial-result delivery is unpredictable across devices.
 *
 * Vosk advantages:
 *   • Truly continuous — single AudioRecord, fed to one Recognizer.
 *   • Silent — no system tones, no mic indicator flicker.
 *   • Offline — wake word matches don't leave the device.
 *   • Free, MIT-licensed.
 *
 * Flow:
 *   1. Ensure Vosk model present (downloads ~40MB on first launch).
 *   2. Open AudioRecord at 16kHz mono PCM.
 *   3. Stream PCM into Vosk Recognizer indefinitely.
 *   4. Each partial result scanned for wake phrase.
 *   5. On match → switch to Sarvam capture for high-quality multilingual
 *      transcription of the actual command, push to TaskQueue, resume.
 */
@AndroidEntryPoint
class WakeWordService : Service() {

    @Inject lateinit var prefs: UserPreferences
    @Inject lateinit var queue: TaskQueue
    @Inject lateinit var worker: TaskWorker
    @Inject lateinit var recorder: AudioRecorder
    @Inject lateinit var stt: SarvamSttClient
    @Inject lateinit var voskModel: VoskModelManager

    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Default)
    private var loopJob: Job? = null
    private var captureJob: Job? = null
    @Volatile private var awake = false
    @Volatile private var wakePhrases: List<String> = listOf("hey sahay", "sahay")
    @Volatile private var locale = "en-IN"

    override fun onCreate() {
        super.onCreate()
        startForegroundCompat("Sahay is starting…")
        worker.ensureRunning()
        scope.launch { initAndRun() }
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int = START_STICKY
    override fun onBind(intent: Intent?): IBinder? = null

    override fun onDestroy() {
        loopJob?.cancel()
        captureJob?.cancel()
        scope.cancel()
        super.onDestroy()
    }

    private suspend fun initAndRun() {
        val snap = prefs.snapshot()
        wakePhrases = buildWakePhrases(snap.assistantName, snap.wakeKeyword)
        locale = snap.voiceLocale
        Log.i(TAG, "wake_phrases=$wakePhrases")

        when (val s = voskModel.ensureModel()) {
            is VoskModelManager.State.Ready -> {
                updateNotif("Sahay listening — say \"${wakePhrases.first()}\"")
                loopJob = scope.launch { runWakeLoop() }
            }
            is VoskModelManager.State.Error -> {
                updateNotif("Sahay couldn't download voice model: ${s.message}")
            }
            is VoskModelManager.State.Downloading -> {
                updateNotif("Downloading voice model… ${s.progress}%")
            }
            VoskModelManager.State.NotDownloaded -> {
                updateNotif("Voice model not yet downloaded.")
            }
        }
    }

    // ─── Vosk continuous loop ─────────────────────────────────────────

    private suspend fun runWakeLoop() {
        val model = voskModel.loadModel() ?: run {
            Log.e(TAG, "vosk_model_load_failed")
            return
        }
        val recognizer = Recognizer(model, SAMPLE_RATE.toFloat())

        val bufSize = AudioRecord.getMinBufferSize(
            SAMPLE_RATE, AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT
        ).coerceAtLeast(8192)

        @Suppress("MissingPermission")
        val audio = AudioRecord(
            MediaRecorder.AudioSource.MIC,
            SAMPLE_RATE,
            AudioFormat.CHANNEL_IN_MONO,
            AudioFormat.ENCODING_PCM_16BIT,
            bufSize * 2,
        )
        if (audio.state != AudioRecord.STATE_INITIALIZED) {
            Log.e(TAG, "audiorecord_not_initialized")
            recognizer.close()
            model.close()
            return
        }

        audio.startRecording()
        val buf = ByteArray(bufSize)
        Log.i(TAG, "wake_loop_started")

        try {
            while (scope.isActive) {
                if (awake) {                                    // capture in progress; wait
                    kotlinx.coroutines.delay(100)
                    continue
                }
                val n = audio.read(buf, 0, buf.size)
                if (n <= 0) continue
                if (recognizer.acceptWaveForm(buf, n)) {
                    val txt = extractText(recognizer.result)
                    if (txt.isNotBlank() && matchesWake(txt)) {
                        Log.i(TAG, "wake_final='$txt'")
                        recognizer.reset()
                        triggerCapture()
                    }
                } else {
                    val txt = extractText(recognizer.partialResult, key = "partial")
                    if (txt.isNotBlank() && matchesWake(txt)) {
                        Log.i(TAG, "wake_partial='$txt'")
                        recognizer.reset()
                        triggerCapture()
                    }
                }
            }
        } finally {
            try { audio.stop() } catch (_: Throwable) {}
            audio.release()
            recognizer.close()
            model.close()
            Log.i(TAG, "wake_loop_stopped")
        }
    }

    // ─── On wake → capture full command via Sarvam ────────────────────

    private fun triggerCapture() {
        if (awake) return
        awake = true
        captureJob?.cancel()
        captureJob = scope.launch {
            try {
                updateNotif("Listening to your command…")
                val wav = recorder.record(maxMillis = 10_000)
                if (wav == null) { Log.w(TAG, "capture_recorder_returned_null"); return@launch }
                val transcription = stt.transcribe(wav, languageHint = locale)
                wav.delete()
                if (!transcription.ok) {
                    Log.w(TAG, "stt_failed err=${transcription.error}")
                    return@launch
                }
                val cleaned = stripWakeWords(transcription.text)
                Log.i(TAG, "captured text='$cleaned' (raw='${transcription.text}')")
                if (cleaned.isNotBlank()) {
                    queue.submit(TaskQueue.Task.Utterance(
                        text = cleaned,
                        locale = transcription.language,
                    ))
                }
            } finally {
                awake = false
                updateNotif("Sahay listening — say \"${wakePhrases.first()}\"")
            }
        }
    }

    // ─── Helpers ──────────────────────────────────────────────────────

    private fun extractText(json: String, key: String = "text"): String =
        try { JSONObject(json).optString(key, "").lowercase().trim() } catch (_: Throwable) { "" }

    private fun matchesWake(text: String): Boolean =
        wakePhrases.any { it.isNotBlank() && text.contains(it) }

    private fun stripWakeWords(text: String): String {
        var out = text.trim()
        for (phrase in wakePhrases.sortedByDescending { it.length }) {
            val idx = out.lowercase().indexOf(phrase)
            if (idx >= 0) {
                out = out.substring(0, idx) + out.substring(idx + phrase.length)
                out = out.trim().trimStart(',', '.', '!', '?').trim()
            }
        }
        return out
    }

    private fun buildWakePhrases(assistantName: String, fallbackKeyword: String): List<String> {
        val name = assistantName.trim().lowercase()
        val list = mutableListOf<String>()
        if (name.isNotBlank()) {
            list += "hey $name"
            list += "ok $name"
            list += name
        }
        val kw = fallbackKeyword.trim().lowercase()
        if (kw.isNotBlank() && kw !in list) {
            list += "hey $kw"
            list += kw
        }
        return list.distinct()
    }

    // ─── Notification ─────────────────────────────────────────────────

    private fun startForegroundCompat(initialText: String) {
        val notif = buildNotif(initialText)
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.UPSIDE_DOWN_CAKE) {
            startForeground(NOTIF_ID, notif, ServiceInfo.FOREGROUND_SERVICE_TYPE_MICROPHONE)
        } else {
            startForeground(NOTIF_ID, notif)
        }
    }

    private fun updateNotif(text: String) {
        val nm = getSystemService(android.app.NotificationManager::class.java) ?: return
        nm.notify(NOTIF_ID, buildNotif(text))
    }

    private fun buildNotif(text: String): Notification {
        val pi = PendingIntent.getActivity(
            this, 0, Intent(this, MainActivity::class.java),
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT,
        )
        return NotificationCompat.Builder(this, App.CHANNEL_FOREGROUND)
            .setSmallIcon(R.mipmap.ic_launcher)
            .setContentTitle("Sahay")
            .setContentText(text)
            .setOngoing(true)
            .setShowWhen(false)
            .setPriority(NotificationCompat.PRIORITY_MIN)
            .setContentIntent(pi)
            .build()
    }

    companion object {
        private const val TAG = "WakeWord"
        private const val NOTIF_ID = 1002
        private const val SAMPLE_RATE = 16_000

        fun start(context: android.content.Context) {
            val i = Intent(context, WakeWordService::class.java)
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) context.startForegroundService(i)
            else context.startService(i)
        }

        fun stop(context: android.content.Context) {
            context.stopService(Intent(context, WakeWordService::class.java))
        }
    }
}
