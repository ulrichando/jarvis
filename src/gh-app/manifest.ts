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
