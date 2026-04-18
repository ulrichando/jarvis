package com.jarvis.android.domain.usecase

import com.jarvis.android.domain.model.ChatEvent
import com.jarvis.android.domain.model.Conversation
import com.jarvis.android.domain.model.Message
import com.jarvis.android.domain.repository.ChatRepository
import kotlinx.coroutines.flow.Flow
import javax.inject.Inject

class SendMessageUseCase @Inject constructor(private val repo: ChatRepository) {
    operator fun invoke(
        conversationId: String,
        content:        String,
        image:          String? = null,
    ): Flow<ChatEvent> = repo.sendMessage(conversationId, content, image)
}

class CreateConversationUseCase @Inject constructor(private val repo: ChatRepository) {
    suspend operator fun invoke(
        title: String = "New conversation",
        model: String = "claude-sonnet-4-6",
    ): Conversation = repo.createConversation(title, model)
}

class ObserveConversationsUseCase @Inject constructor(private val repo: ChatRepository) {
    operator fun invoke(): Flow<List<Conversation>> = repo.observeConversations()
}

class ObserveMessagesUseCase @Inject constructor(private val repo: ChatRepository) {
    operator fun invoke(conversationId: String): Flow<List<Message>> =
        repo.observeMessages(conversationId)
}

class DeleteConversationUseCase @Inject constructor(private val repo: ChatRepository) {
    suspend operator fun invoke(id: String) = repo.deleteConversation(id)
}

class RenameConversationUseCase @Inject constructor(private val repo: ChatRepository) {
    suspend operator fun invoke(id: String, title: String) = repo.renameConversation(id, title)
}

class PinConversationUseCase @Inject constructor(private val repo: ChatRepository) {
    suspend operator fun invoke(id: String, pinned: Boolean) = repo.pinConversation(id, pinned)
}
