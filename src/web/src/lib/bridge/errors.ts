import { NextResponse } from 'next/server'

/**
 * Build a CCR-compatible error response. Shape matches what the CLI's
 * `extractErrorDetail` / `extractErrorTypeFromData` parsers expect, so
 * the existing client error handling works unchanged.
 */
export function bridgeError(
  status: number,
  type: string,
  detail?: string,
): NextResponse {
  // Include both `detail` and `message` so the CLI's two parsers both
  // find the human-readable text:
  //   - `extractErrorTypeFromData` reads `error.type`
  //   - `extractErrorDetail` reads `error.message` / `data.message`
  const body: { error: { type: string; detail?: string; message?: string } } = {
    error: detail ? { type, detail, message: detail } : { type },
  }
  return NextResponse.json(body, { status })
}
