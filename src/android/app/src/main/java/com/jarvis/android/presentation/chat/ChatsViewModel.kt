package com.jarvis.android.presentation.chat

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.jarvis.android.domain.model.Conversation
import com.jarvis.android.domain.usecase.CreateConversationUseCase
import com.jarvis.android.domain.usecase.DeleteConversationUseCase
import com.jarvis.android.domain.usecase.ObserveConversationsUseCase
import com.jarvis.android.domain.usecase.PinConversationUseCase
import com.jarvis.android.domain.usecase.RenameConversationUseCase
import dagger.hilt.android.lifecycle.HiltViewModel
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.launchIn
import kotlinx.coroutines.flow.onEach
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import javax.inject.Inject

data class ChatsUiState(
    val conversations: List<Conversation> = emptyList(),
    val query:         String             = "",
    /** Non-empty when the user is in multi-select mode (long-pressed a row). */
    val selectedIds:   Set<String>        = emptySet(),
) {
    val selectionMode: Boolean get() = selectedIds.isNotEmpty()
}

/**
 * View-model for the dedicated Chats screen — Claude-style full-page list
 * of every conversation with search + pin/delete/rename, plus a multi-select
 * mode for bulk deletion. All mutations route through existing use cases
 * so Room stays the single source of truth (the drawer + chat view
 * re-render via the same flow).
 */
@HiltViewModel
class ChatsViewModel @Inject constructor(
    private val observeConversations: ObserveConversationsUseCase,
    private val createConversation:   CreateConversationUseCase,
    private val deleteConversation:   DeleteConversationUseCase,
    private val renameConversation:   RenameConversationUseCase,
    private val pinConversation:      PinConversationUseCase,
) : ViewModel() {

    private val _uiState = MutableStateFlow(ChatsUiState())
    val uiState: StateFlow<ChatsUiState> = _uiState.asStateFlow()

    init {
        observeConversations()
            .onEach { list ->
                _uiState.update { s ->
                    // Prune selected ids that no longer exist (after a delete).
                    val live = list.map { it.id }.toSet()
                    s.copy(
                        conversations = list,
                        selectedIds   = s.selectedIds.intersect(live),
                    )
                }
            }
            .launchIn(viewModelScope)
    }

    fun onQueryChange(q: String) = _uiState.update { it.copy(query = q) }

    fun newConversation(onCreated: (Conversation) -> Unit) {
        viewModelScope.launch {
            val conv = createConversation()
            onCreated(conv)
        }
    }

    fun rename(id: String, title: String) {
        viewModelScope.launch { renameConversation(id, title) }
    }

    fun togglePin(id: String, pinned: Boolean) {
        viewModelScope.launch { pinConversation(id, pinned) }
    }

    fun delete(id: String) {
        viewModelScope.launch { deleteConversation(id) }
    }

    // ── Multi-select ──────────────────────────────────────────────────────

    fun toggleSelection(id: String) = _uiState.update { s ->
        val next = s.selectedIds.toMutableSet().apply {
            if (contains(id)) remove(id) else add(id)
        }
        s.copy(selectedIds = next)
    }

    fun clearSelection() = _uiState.update { it.copy(selectedIds = emptySet()) }

    /** Select every conversation currently matching the active search query. */
    fun selectAllVisible(visibleIds: List<String>) = _uiState.update {
        it.copy(selectedIds = visibleIds.toSet())
    }

    fun deleteSelected() {
        val ids = _uiState.value.selectedIds
        if (ids.isEmpty()) return
        viewModelScope.launch {
            ids.forEach { id -> deleteConversation(id) }
            _uiState.update { it.copy(selectedIds = emptySet()) }
        }
    }
}
