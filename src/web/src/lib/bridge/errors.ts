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
  const body: { error: { type: string; detail?: string } } = {
    error: detail ? { type, detail } : { type },
  }
  return NextResponse.json(body, { status })
}
