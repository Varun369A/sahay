package io.sahay.service

import android.app.Notification
import android.app.PendingIntent
import android.app.Service
import android.content.Intent
import android.content.pm.ServiceInfo
import android.os.Build
import android.os.Bundle
import android.os.Handler
import android.os.IBinder
import android.os.Looper
import android.speech.RecognitionListener
import android.speech.RecognizerIntent
import android.speech.SpeechRecognizer
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
import javax.inject.Inject
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.launch

/**
 * Always-on wake-word listener using Android's built-in SpeechRecognizer.
 *
 * No third-party SDK / API key required — Google's on-device voice
 * recognition (already on every Android phone) does the heavy lifting.
 *
 * Strategy:
 *   • Continuous SpeechRecognizer with partial results enabled
 *   • Each partial result is scanned for the user's wake phrase
 *     (e.g. "hey sahay", "hey jarvis", or whatever they set in Settings)
 *   • On match → cancel the wake recognizer → high-quality Sarvam capture
 *     of the remainder of the utterance → push to TaskQueue
 *   • Restart the wake recognizer for the next cycle
 *
 * Battery: ~3-5%/hr while running. Acceptable for a personal assistant.
 *
 * Concurrency: TaskWorker pops the queue and runs Accessibility-driven
 * automation in its own coroutine. The mic loop here is not blocked
 * waiting for the previous task — that's the "hear + act simultaneously"
 * design the user asked for.
 */
@AndroidEntryPoint
class WakeWordService : Service() {

    @Inject lateinit var prefs: UserPreferences
    @Inject lateinit var queue: TaskQueue
    @Inject lateinit var worker: TaskWorker
    @Inject lateinit var recorder: AudioRecorder
    @Inject lateinit var stt: SarvamSttClient

    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Default)
    private val main = Handler(Looper.getMainLooper())
    private var recognizer: SpeechRecognizer? = null
    private var captureJob: Job? = null
    @Volatile private var awake = false
    @Volatile private var wakePhrases: List<String> = listOf("hey sahay", "sahay")
    @Volatile private var locale = "en-IN"

    override fun onCreate() {
        super.onCreate()
        startForegroundCompat()
        worker.ensureRunning()
        scope.launch {
            val snap = prefs.snapshot()
            wakePhrases = buildWakePhrases(snap.assistantName, snap.wakeKeyword)
            locale = snap.voiceLocale
            main.post { startRecognizer() }
        }
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int = START_STICKY
    override fun onBind(intent: Intent?): IBinder? = null

    override fun onDestroy() {
        try { recognizer?.cancel(); recognizer?.destroy() } catch (_: Throwable) {}
        recognizer = null
        captureJob?.cancel()
        scope.cancel()
        super.onDestroy()
    }

    // ─── Recognizer lifecycle ─────────────────────────────────────────

    private fun startRecognizer() {
        if (!SpeechRecognizer.isRecognitionAvailable(this)) {
            Log.e(TAG, "SpeechRecognizer not available on this device")
            stopSelf()
            return
        }
        recognizer?.cancel()
        recognizer = SpeechRecognizer.createSpeechRecognizer(this).apply {
            setRecognitionListener(listener)
        }
        val intent = buildRecognizerIntent()
        recognizer?.startListening(intent)
        Log.i(TAG, "wake_recognizer_started phrases=$wakePhrases")
    }

    private fun buildRecognizerIntent(): Intent =
        Intent(RecognizerIntent.ACTION_RECOGNIZE_SPEECH).apply {
            putExtra(RecognizerIntent.EXTRA_LANGUAGE_MODEL, RecognizerIntent.LANGUAGE_MODEL_FREE_FORM)
            putExtra(RecognizerIntent.EXTRA_LANGUAGE, locale)
            putExtra(RecognizerIntent.EXTRA_LANGUAGE_PREFERENCE, locale)
            putExtra(RecognizerIntent.EXTRA_PARTIAL_RESULTS, true)
            putExtra(RecognizerIntent.EXTRA_PREFER_OFFLINE, true)
            putExtra(RecognizerIntent.EXTRA_SPEECH_INPUT_COMPLETE_SILENCE_LENGTH_MILLIS, 1500)
            putExtra(RecognizerIntent.EXTRA_SPEECH_INPUT_MINIMUM_LENGTH_MILLIS, 1500)
        }

    private val listener = object : RecognitionListener {
        override fun onReadyForSpeech(params: Bundle?) {}
        override fun onBeginningOfSpeech() {}
        override fun onRmsChanged(rmsdB: Float) {}
        override fun onBufferReceived(buffer: ByteArray?) {}
        override fun onEndOfSpeech() {}
        override fun onEvent(eventType: Int, params: Bundle?) {}

        override fun onPartialResults(partialResults: Bundle?) {
            val text = partialResults?.getStringArrayList(SpeechRecognizer.RESULTS_RECOGNITION)
                ?.joinToString(" ")
                ?.lowercase()
                .orEmpty()
            if (text.isBlank()) return
            if (matchesWake(text)) {
                Log.i(TAG, "wake_detected partial='$text'")
                onWake()
            }
        }

        override fun onResults(results: Bundle?) {
            val text = results?.getStringArrayList(SpeechRecognizer.RESULTS_RECOGNITION)
                ?.firstOrNull()?.lowercase().orEmpty()
            if (text.isNotBlank() && matchesWake(text)) {
                Log.i(TAG, "wake_detected final='$text'")
                onWake()
                return
            }
            // No wake — just restart the cycle.
            restartLater(120)
        }

        override fun onError(error: Int) {
            // Errors include NO_MATCH (7), SPEECH_TIMEOUT (6) — normal, just restart.
            restartLater(if (error == SpeechRecognizer.ERROR_RECOGNIZER_BUSY) 600 else 200)
        }
    }

    private fun restartLater(delayMs: Long) {
        if (awake) return                       // capture in progress — don't restart yet
        main.postDelayed({ if (!awake) startRecognizer() }, delayMs)
    }

    // ─── On wake → capture full command via Sarvam ────────────────────

    private fun onWake() {
        if (awake) return
        awake = true
        try { recognizer?.cancel() } catch (_: Throwable) {}
        captureJob?.cancel()
        captureJob = scope.launch {
            try {
                val wav = recorder.record(maxMillis = 10_000)
                if (wav == null) { Log.w(TAG, "capture_recorder_returned_null"); return@launch }
                val transcription = stt.transcribe(wav, languageHint = locale)
                wav.delete()
                if (!transcription.ok) {
                    Log.w(TAG, "stt_failed err=${transcription.error}")
                    return@launch
                }
                Log.i(TAG, "captured text='${transcription.text}'")
                queue.submit(TaskQueue.Task.Utterance(
                    text = stripWakeWords(transcription.text),
                    locale = transcription.language,
                ))
            } finally {
                awake = false
                main.post { startRecognizer() }
            }
        }
    }

    // ─── Wake-word matching ───────────────────────────────────────────

    private fun matchesWake(text: String): Boolean =
        wakePhrases.any { it.isNotBlank() && text.contains(it) }

    /** Remove leading "hey sahay" / "sahay" so the actual command goes to dispatch. */
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
        if (fallbackKeyword.isNotBlank() && fallbackKeyword.lowercase() !in list) {
            list += "hey $fallbackKeyword"
            list += fallbackKeyword.lowercase()
        }
        return list
    }

    // ─── Foreground notification ──────────────────────────────────────

    private fun startForegroundCompat() {
        val pi = PendingIntent.getActivity(
            this, 0, Intent(this, MainActivity::class.java),
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT,
        )
        val notif: Notification = NotificationCompat.Builder(this, App.CHANNEL_FOREGROUND)
            .setSmallIcon(R.mipmap.ic_launcher)
            .setContentTitle("Sahay is listening")
            .setContentText("Say the wake word to activate.")
            .setOngoing(true)
            .setShowWhen(false)
            .setPriority(NotificationCompat.PRIORITY_MIN)
            .setContentIntent(pi)
            .build()
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.UPSIDE_DOWN_CAKE) {
            startForeground(NOTIF_ID, notif, ServiceInfo.FOREGROUND_SERVICE_TYPE_MICROPHONE)
        } else {
            startForeground(NOTIF_ID, notif)
        }
    }

    companion object {
        private const val TAG = "WakeWord"
        private const val NOTIF_ID = 1002

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
