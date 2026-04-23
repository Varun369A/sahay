package io.sahay.ui.screens.files

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import dagger.hilt.android.lifecycle.HiltViewModel
import io.sahay.data.api.FileOut
import io.sahay.data.repository.AppRepository
import javax.inject.Inject
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch

data class FilesState(
    val files: List<FileOut> = emptyList(),
    val isLoading: Boolean = false,
    val isGenerating: Boolean = false,
    val errorKey: String? = null,
    val folder: String = "/",
    val prompt: String = "",
)

@HiltViewModel
class FilesViewModel @Inject constructor(
    private val repo: AppRepository,
) : ViewModel() {

    private val _state = MutableStateFlow(FilesState())
    val state: StateFlow<FilesState> = _state.asStateFlow()

    init { refresh() }

    fun refresh() {
        viewModelScope.launch {
            _state.update { it.copy(isLoading = true, errorKey = null) }
            runCatching { repo.listFiles(_state.value.folder) }
                .onSuccess { files -> _state.update { it.copy(isLoading = false, files = files) } }
                .onFailure { t -> _state.update { it.copy(isLoading = false, errorKey = t.message) } }
        }
    }

    fun onPromptChange(value: String) = _state.update { it.copy(prompt = value) }

    fun generate() {
        val prompt = _state.value.prompt.trim()
        if (prompt.isEmpty()) return
        viewModelScope.launch {
            _state.update { it.copy(isGenerating = true, errorKey = null) }
            runCatching { repo.generateImage(prompt = prompt, folder = _state.value.folder) }
                .onSuccess {
                    _state.update { it.copy(isGenerating = false, prompt = "") }
                    refresh()
                }
                .onFailure { t -> _state.update { it.copy(isGenerating = false, errorKey = t.message) } }
        }
    }

    fun delete(id: String) {
        viewModelScope.launch {
            runCatching { repo.deleteFile(id) }.onSuccess { refresh() }
        }
    }
}
