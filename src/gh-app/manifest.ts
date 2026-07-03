// src/gh-app/manifest.ts — GitHub App manifest + setup page.
//
// The manifest flow makes app creation near-one-click: GET /setup serves a
// form that POSTs the manifest to github.com/settings/apps/new; GitHub
// creates the (private) app and redirects back to /setup/callback?code=…,
// which Task 2's convertManifestCode exchanges for the app credentials.
export type AppManifest = {
  name: string; url: string; public: boolean
  hook_attributes: { url: string; active: boolean }
  redirect_url: string
  default_permissions: Record<string, string>
  default_events: string[]
}

export function buildManifest(base: string): AppManifest {
  return {
    name: 'jarvis', url: base, public: false,
    hook_attributes: { url: `${base}/webhook`, active: true },
    redirect_url: `${base}/setup/callback`,
    default_permissions: { contents: 'write', pull_requests: 'write', issues: 'write', metadata: 'read' },
    default_events: ['issue_comment', 'issues', 'pull_request_review_comment'],
  }
}

// setup page: an HTML form that POSTs `manifest=<json>` to github.com/settings/apps/new
export function setupPageHtml(base: string): string {
  const m = JSON.stringify(buildManifest(base))
  return `<!doctype html><form action="https://github.com/settings/apps/new" method="post">
<input type="hidden" name="manifest" value='${m.replace(/'/g, '&#39;')}'>
<button type="submit">Create JARVIS App</button></form>`
}

// --- Manifest-code → credentials (the /setup/callback exchange) ---
//
// GitHub redirects back with a short-lived `code`; one POST converts it into
// the app's permanent credentials. Injected fetch/saveCreds so tests never
// touch GitHub or disk; the server wires saveCreds to a gitignored
// ~/.jarvis/gh-app/creds.json (VPS) — never to the repo.
export type AppCreds = {
  appId: number
  pem: string
  webhookSecret: string
  clientId?: string
}

export type ConvertDeps = {
  fetch: typeof fetch
  saveCreds?: (creds: AppCreds) => Promise<void> | void
}

export async function convertManifestCode(code: string, deps: ConvertDeps): Promise<AppCreds> {
  const url = `https://api.github.com/app-manifests/${encodeURIComponent(code)}/conversions`
  const res = await deps.fetch(url, {
    method: 'POST',
    headers: { Accept: 'application/vnd.github+json' },
  })
  if (!res.ok) {
    // Body deliberately NOT included: conversion errors are safe, but keep the
    // failure line short + log-greppable.
    throw new Error(`manifest conversion failed: HTTP ${res.status}`)
  }
  const raw = (await res.json()) as { id?: unknown; pem?: unknown; webhook_secret?: unknown; client_id?: unknown }
  if (typeof raw.id !== 'number' || typeof raw.pem !== 'string' || typeof raw.webhook_secret !== 'string') {
    throw new Error('manifest conversion: response missing id/pem/webhook_secret')
  }
  const creds: AppCreds = {
    appId: raw.id,
    pem: raw.pem,
    webhookSecret: raw.webhook_secret,
    ...(typeof raw.client_id === 'string' ? { clientId: raw.client_id } : {}),
  }
  await deps.saveCreds?.(creds)
  return creds
}
