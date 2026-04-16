// Stub: filePersistence types

export const DEFAULT_UPLOAD_CONCURRENCY = 5
export const FILE_COUNT_LIMIT = 1000
export const OUTPUTS_SUBDIR = 'outputs'

export type TurnStartTime = number

export type PersistedFile = {
  path: string
  fileId: string
}

export type FailedPersistence = {
  path: string
  error: string
}

export type FilesPersistedEventData = {
  persisted: PersistedFile[]
  failed: FailedPersistence[]
  turnStartTime: TurnStartTime
}
