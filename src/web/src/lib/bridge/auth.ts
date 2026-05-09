/**
 * Parse `Authorization: Bearer <token>`. Returns the token or null if the
 * header is missing, uses a different scheme, or has an empty token.
 */
export function extractBearer(header: string | null): string | null {
  if (!header) return null
  const match = /^bearer\s+(\S+)\s*$/i.exec(header.trim())
  return match ? match[1] : null
}
