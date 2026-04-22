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
)

/**
 * View-model for the dedicated Chats screen — Claude-style full-page list
 * of every conversation with search + pin/delete/rename. Reuses the existing
 * ChatRepository use cases so its state stays in lock-step with the drawer
 * and the Chat view (Room flow under the hood).
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
            .onEach { list -> _uiState.update { it.copy(conversations = list) } }
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
}
