package com.jarvis.android.presentation.filesystem

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.jarvis.android.domain.model.FileItem
import com.jarvis.android.domain.usecase.CreateDirectoryUseCase
import com.jarvis.android.domain.usecase.DeleteFileUseCase
import com.jarvis.android.domain.usecase.ListDirectoryUseCase
import com.jarvis.android.domain.usecase.ReadFileUseCase
import dagger.hilt.android.lifecycle.HiltViewModel
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import javax.inject.Inject

data class FileManagerUiState(
    val path:         String           = "/sdcard",
    val breadcrumbs:  List<String>     = listOf("/sdcard"),
    val items:        List<FileItem>   = emptyList(),
    val isLoading:    Boolean          = false,
    val isRootMode:   Boolean          = false,
    val error:        String?          = null,
    val selectedItem: FileItem?        = null,
    val fileContent:  String?          = null,   // non-null when viewing a file
    val showNewDirDialog: Boolean      = false,
)

sealed class FileManagerIntent {
    data class Navigate(val path: String) : FileManagerIntent()
    object NavigateUp : FileManagerIntent()
    object ToggleRoot : FileManagerIntent()
    data class OpenFile(val item: FileItem) : FileManagerIntent()
    data class SelectItem(val item: FileItem?) : FileManagerIntent()
    data class DeleteItem(val item: FileItem) : FileManagerIntent()
    data class CreateDirectory(val name: String) : FileManagerIntent()
    object ShowNewDirDialog : FileManagerIntent()
    object DismissNewDirDialog : FileManagerIntent()
    object CloseFile : FileManagerIntent()
    object ClearError : FileManagerIntent()
}

@HiltViewModel
class FileManagerViewModel @Inject constructor(
    private val listDirectory:   ListDirectoryUseCase,
    private val readFile:        ReadFileUseCase,
    private val deleteFile:      DeleteFileUseCase,
    private val createDirectory: CreateDirectoryUseCase,
) : ViewModel() {

    private val _uiState = MutableStateFlow(FileManagerUiState())
    val uiState: StateFlow<FileManagerUiState> = _uiState.asStateFlow()

    init { load("/sdcard") }

    fun onIntent(intent: FileManagerIntent) = when (intent) {
        is FileManagerIntent.Navigate       -> load(intent.path)
        is FileManagerIntent.NavigateUp     -> navigateUp()
        is FileManagerIntent.ToggleRoot     -> toggleRoot()
        is FileManagerIntent.OpenFile       -> openFile(intent.item)
        is FileManagerIntent.SelectItem     -> _uiState.update { it.copy(selectedItem = intent.item) }
        is FileManagerIntent.DeleteItem     -> deleteItem(intent.item)
        is FileManagerIntent.CreateDirectory -> createDir(intent.name)
        is FileManagerIntent.ShowNewDirDialog  -> _uiState.update { it.copy(showNewDirDialog = true) }
        is FileManagerIntent.DismissNewDirDialog -> _uiState.update { it.copy(showNewDirDialog = false) }
        is FileManagerIntent.CloseFile      -> _uiState.update { it.copy(fileContent = null) }
        is FileManagerIntent.ClearError     -> _uiState.update { it.copy(error = null) }
    }

    private fun load(path: String) {
        viewModelScope.launch {
            _uiState.update { it.copy(isLoading = true, error = null) }
            val result = listDirectory(path, asRoot = _uiState.value.isRootMode)
            result.fold(
                onSuccess = { items ->
                    val crumbs = buildBreadcrumbs(path)
                    _uiState.update {
                        it.copy(
                            path        = path,
                            breadcrumbs = crumbs,
                            items       = items,
                            isLoading   = false,
                        )
                    }
                },
                onFailure = { e ->
                    _uiState.update { it.copy(isLoading = false, error = e.message) }
                },
            )
        }
    }

    private fun navigateUp() {
        val current = _uiState.value.path
        val parent  = current.substringBeforeLast('/', "/")
        if (parent != current) load(parent.ifEmpty { "/" })
    }

    private fun toggleRoot() {
        val newRoot = !_uiState.value.isRootMode
        _uiState.update { it.copy(isRootMode = newRoot) }
        load(_uiState.value.path)
    }

    private fun openFile(item: FileItem) {
        if (item.isDirectory) { load(item.path); return }
        viewModelScope.launch {
            _uiState.update { it.copy(isLoading = true) }
            readFile(item.path, asRoot = _uiState.value.isRootMode).fold(
                onSuccess = { text -> _uiState.update { it.copy(isLoading = false, fileContent = text) } },
                onFailure = { e  -> _uiState.update { it.copy(isLoading = false, error = e.message) } },
            )
        }
    }

    private fun deleteItem(item: FileItem) {
        viewModelScope.launch {
            deleteFile(item.path, asRoot = _uiState.value.isRootMode).fold(
                onSuccess = { load(_uiState.value.path) },
                onFailure = { e -> _uiState.update { it.copy(error = e.message) } },
            )
        }
    }

    private fun createDir(name: String) {
        val path = "${_uiState.value.path}/$name"
        viewModelScope.launch {
            createDirectory(path, asRoot = _uiState.value.isRootMode).fold(
                onSuccess = { load(_uiState.value.path) },
                onFailure = { e -> _uiState.update { it.copy(error = e.message) } },
            )
        }
        _uiState.update { it.copy(showNewDirDialog = false) }
    }

    private fun buildBreadcrumbs(path: String): List<String> {
        if (path == "/") return listOf("/")
        val parts = path.removePrefix("/").split("/")
        return listOf("/") + parts.runningFold("") { acc, p ->
            if (acc.isEmpty()) "/$p" else "$acc/$p"
        }.drop(1)
    }
}
