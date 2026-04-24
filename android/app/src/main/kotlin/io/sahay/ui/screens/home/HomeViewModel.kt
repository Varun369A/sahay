package io.sahay.ui.screens.home

import android.content.Context
import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import dagger.hilt.android.lifecycle.HiltViewModel
import dagger.hilt.android.qualifiers.ApplicationContext
import io.sahay.engine.IntentParser
import io.sahay.service.SahayForegroundService
import io.sahay.voice.AudioRecorder
import io.sahay.voice.SarvamSttClient
import javax.inject.Inject
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonObject

data class HomeState(
    val mode: Mode = Mode.Idle,
    val transcript: String = "",
    val lastResult: String = "",
    val errorKey: String? = null,
) {
    enum class Mode { Idle, Listening, Thinking, Running }
}

@HiltViewModel
class HomeViewModel @Inject constructor(
    @ApplicationContext private val context: Context,
    private val recorder: AudioRecorder,
    private val stt: SarvamSttClient,
    private val parser: IntentParser,
    private val json: Json,
) : ViewModel() {

    private val _state = MutableStateFlow(HomeState())
    val state: StateFlow<HomeState> = _state.asStateFlow()

    fun startListening(localeHint: String? = null) {
        if (_state.value.mode != HomeState.Mode.Idle) return
        _state.update { it.copy(mode = HomeState.Mode.Listening, transcript = "", errorKey = null) }
        viewModelScope.launch {
            val wav = recorder.record(maxMillis = 15_000) ?: run {
                _state.update { it.copy(mode = HomeState.Mode.Idle, errorKey = "record_failed") }
                return@launch
            }
            _state.update { it.copy(mode = HomeState.Mode.Thinking) }

            val transcription = stt.transcribe(wav, languageHint = localeHint)
            wav.delete()

            if (!transcription.ok) {
                _state.update { it.copy(mode = HomeState.Mode.Idle, errorKey = "stt_failed:${transcription.error}") }
                return@launch
            }
            _state.update { it.copy(transcript = transcription.text) }

            // Full-auto: hand the raw utterance to the Claude-driven AgentLoop.
            // The agent decides everything — which app, what steps, when done.
            _state.update { it.copy(mode = HomeState.Mode.Running) }
            SahayForegroundService.runAgent(
                context = context,
                goal = transcription.text,
            )
            _state.update {
                it.copy(mode = HomeState.Mode.Idle, lastResult = "Running: ${transcription.text}")
            }
        }
    }

    fun stopListening() {
        if (_state.value.mode == HomeState.Mode.Listening) {
            recorder.stop()
        }
    }
}
