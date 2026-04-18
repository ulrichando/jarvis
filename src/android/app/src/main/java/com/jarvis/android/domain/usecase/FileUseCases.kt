package com.jarvis.android.domain.usecase

import com.jarvis.android.domain.model.FileItem
import com.jarvis.android.domain.model.FileStats
import com.jarvis.android.domain.repository.FileRepository
import javax.inject.Inject

class ListDirectoryUseCase @Inject constructor(private val repo: FileRepository) {
    suspend operator fun invoke(path: String, asRoot: Boolean = false): Result<List<FileItem>> =
        repo.listDirectory(path, asRoot)
}

class ReadFileUseCase @Inject constructor(private val repo: FileRepository) {
    suspend operator fun invoke(
        path: String, maxBytes: Int = 65536, asRoot: Boolean = false,
    ): Result<String> = repo.readFile(path, maxBytes, asRoot)
}

class WriteFileUseCase @Inject constructor(private val repo: FileRepository) {
    suspend operator fun invoke(
        path: String, content: String, append: Boolean = false, asRoot: Boolean = false,
    ): Result<Unit> = repo.writeFile(path, content, append, asRoot)
}

class DeleteFileUseCase @Inject constructor(private val repo: FileRepository) {
    suspend operator fun invoke(path: String, asRoot: Boolean = false): Result<Unit> =
        repo.deleteFile(path, asRoot)
}

class MoveFileUseCase @Inject constructor(private val repo: FileRepository) {
    suspend operator fun invoke(from: String, to: String, asRoot: Boolean = false): Result<Unit> =
        repo.moveFile(from, to, asRoot)
}

class GetFileStatsUseCase @Inject constructor(private val repo: FileRepository) {
    suspend operator fun invoke(path: String): Result<FileStats> = repo.getStats(path)
}

class CreateDirectoryUseCase @Inject constructor(private val repo: FileRepository) {
    suspend operator fun invoke(path: String, asRoot: Boolean = false): Result<Unit> =
        repo.createDirectory(path, asRoot)
}
