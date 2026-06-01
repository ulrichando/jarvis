// SendUserFileTool prompt — stub
export const SEND_USER_FILE_TOOL_NAME = 'SendUserFile'

export function getSendUserFilePrompt(): string {
  return 'Send a file to the user. Provide the file path and an optional message.'
}

export function buildSendUserFileDescription(): string {
  return 'Send a file from the local filesystem to the user.'
}
