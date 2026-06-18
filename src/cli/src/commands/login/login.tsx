import * as React from 'react'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'

import {
  applyJarvisToken,
  resolveServerRoot,
} from '../../cli/handlers/jarvisAuth.js'
import type { LocalJSXCommandContext } from '../../commands.js'
import { Dialog } from '../../components/design-system/Dialog.js'
import { Spinner } from '../../components/Spinner.js'
import TextInput from '../../components/TextInput.js'
import { useTerminalSize } from '../../hooks/useTerminalSize.js'
import { Box, Text } from '../../ink.js'
import { setClipboard } from '../../ink/termio/osc.js'
import { useKeybinding } from '../../keybindings/useKeybinding.js'
import { AuthCodeListener } from '../../services/oauth/auth-code-listener.js'
import { generateState } from '../../services/oauth/crypto.js'
import type { LocalJSXCommandOnDone } from '../../types/command.js'
import { openBrowser } from '../../utils/browser.js'
import { resetUserCache } from '../../utils/user.js'

const PASTE_MSG = 'Or paste your token here > '

// Served to the browser tab once the loopback handoff completes, so the user
// gets visible confirmation in the browser (mirrors Claude Code's OAuth
// success page) before returning to the terminal.
const SUCCESS_HTML = [
  '<!doctype html><html><head><meta charset="utf-8"><title>Jarvis CLI</title>',
  '<style>body{font-family:system-ui,-apple-system,sans-serif;background:#0b0b0c;',
  'color:#e8e8e8;display:flex;align-items:center;justify-content:center;height:100vh;',
  'margin:0}.c{text-align:center}.k{color:#34d399;font-size:44px;line-height:1}',
  'h2{font-weight:600;margin:.6rem 0 .3rem}p{color:#9b9b9b;margin:0}</style></head>',
  '<body><div class="c"><div class="k">✓</div><h2>Signed in to Jarvis CLI</h2>',
  '<p>You can close this tab and return to your terminal.</p></div></body></html>',
].join('')

type Status =
  | { state: 'connecting' }
  | { state: 'waiting'; url: string }
  | { state: 'applying' }
  | { state: 'success'; serverRoot: string; proxyMinted: boolean }
  | { state: 'error'; message: string }

export async function call(
  onDone: LocalJSXCommandOnDone,
  context: LocalJSXCommandContext,
): Promise<React.ReactNode> {
  return <JarvisLogin onDone={onDone} context={context} />
}

// Exported so the interactive startup gate (interactiveHelpers.tsx) can render
// the same login flow when login is required and no token is configured. In
// that pre-REPL context there is no live API client yet, so `context` is
// omitted — the freshly-persisted token is picked up when the client is built
// right after this gate.
export function JarvisLogin({
  onDone,
  context,
}: {
  onDone: LocalJSXCommandOnDone
  context?: LocalJSXCommandContext
}): React.ReactNode {
  const serverRoot = useMemo(() => resolveServerRoot(undefined), [])
  const [status, setStatus] = useState<Status>({ state: 'connecting' })
  const [pasted, setPasted] = useState('')
  const [cursorOffset, setCursorOffset] = useState(0)
  const [copied, setCopied] = useState(false)
  const [attempt, setAttempt] = useState(0)
  const columns = useTerminalSize().columns - PASTE_MSG.length - 1

  // Resolves the in-flight login when the user pastes a token manually,
  // racing against the automatic loopback capture (see the effect below).
  const manualResolverRef = useRef<((token: string) => void) | null>(null)

  // Login orchestration: start a localhost loopback listener, open the browser
  // to the JARVIS /cli-auth page, and race the automatic redirect capture
  // against a manual token paste. Re-runs on retry (`attempt`).
  useEffect(() => {
    let cancelled = false
    const listener = new AuthCodeListener('/callback')
    void (async () => {
      try {
        const port = await listener.start()
        const state = generateState()
        const redirectUri = `http://localhost:${port}/callback`
        const url = `${serverRoot}/cli-auth?redirect_uri=${encodeURIComponent(
          redirectUri,
        )}&state=${encodeURIComponent(state)}`
        if (cancelled) return
        setStatus({ state: 'waiting', url })

        const bridgeToken = await new Promise<string>((resolve, reject) => {
          manualResolverRef.current = resolve
          listener
            .waitForAuthorization(state, async () => {
              await openBrowser(url)
            })
            .then(resolve)
            .catch(reject)
        })
        manualResolverRef.current = null
        if (cancelled) return

        // Confirm the handoff in the browser tab (no-op for the manual-paste
        // path, which never hit the loopback).
        listener.handleSuccessRedirect([], res => {
          res.writeHead(200, { 'Content-Type': 'text/html' })
          res.end(SUCCESS_HTML)
        })

        setStatus({ state: 'applying' })
        const result = await applyJarvisToken({ serverRoot, bridgeToken })
        // Make the new proxy token take effect in this live session: rebuild
        // the API client and re-fetch auth-dependent data (mirrors /login).
        // Skipped at startup (no `context`) — the client is built afterward.
        if (context) {
          context.onChangeAPIKey()
          resetUserCache()
          context.setAppState(prev => ({
            ...prev,
            authVersion: prev.authVersion + 1,
          }))
        }
        if (!cancelled) {
          setStatus({
            state: 'success',
            serverRoot: result.serverRoot,
            proxyMinted: result.proxyMinted,
          })
        }
      } catch (err) {
        if (!cancelled) {
          setStatus({
            state: 'error',
            message: err instanceof Error ? err.message : String(err),
          })
        }
      }
    })()
    return () => {
      cancelled = true
      manualResolverRef.current = null
      listener.close()
    }
  }, [serverRoot, attempt, context])

  // "press c to copy" the URL while waiting (mirrors ConsoleOAuthFlow).
  useEffect(() => {
    if (status.state === 'waiting' && pasted === 'c' && !copied) {
      void setClipboard(status.url).then(raw => {
        if (raw) process.stdout.write(raw)
        setCopied(true)
        setTimeout(() => setCopied(false), 2000)
      })
      setPasted('')
    }
  }, [pasted, status, copied])

  const submitPaste = useCallback((value: string) => {
    const token = value.trim()
    if (token && manualResolverRef.current) {
      manualResolverRef.current(token)
      manualResolverRef.current = null
    }
  }, [])

  // Enter to dismiss on success.
  useKeybinding('confirm:yes', () => onDone(`Signed in to ${serverRoot}.`), {
    context: 'Confirmation',
    isActive: status.state === 'success',
  })
  // Enter to retry on error.
  useKeybinding(
    'confirm:yes',
    () => {
      setPasted('')
      setStatus({ state: 'connecting' })
      setAttempt((a: number) => a + 1)
    },
    { context: 'Confirmation', isActive: status.state === 'error' },
  )

  let body: React.ReactNode = null
  switch (status.state) {
    case 'connecting':
      body = (
        <Box>
          <Spinner />
          <Text>Starting sign-in…</Text>
        </Box>
      )
      break
    case 'waiting':
      body = (
        <Box flexDirection="column" gap={1}>
          <Text>
            Opened your browser to sign in to <Text bold>{serverRoot}</Text>.
            Approve there and you’ll return automatically.
          </Text>
          <Text dimColor>
            Didn’t open? Visit {status.url} {copied ? '(copied)' : '(press c to copy)'}
          </Text>
          <Box>
            <Text>{PASTE_MSG}</Text>
            <TextInput
              value={pasted}
              onChange={setPasted}
              onSubmit={submitPaste}
              cursorOffset={cursorOffset}
              onChangeCursorOffset={setCursorOffset}
              columns={columns}
              mask="*"
            />
          </Box>
        </Box>
      )
      break
    case 'applying':
      body = (
        <Box>
          <Spinner />
          <Text>Saving credentials…</Text>
        </Box>
      )
      break
    case 'success':
      body = (
        <Box flexDirection="column" gap={1}>
          <Text color="success">
            Signed in to {status.serverRoot}. Press <Text bold>Enter</Text> to
            continue.
          </Text>
          <Text dimColor>
            {status.proxyMinted
              ? 'Proxy authentication is enabled — it takes full effect in your next jarvis session.'
              : 'The server issued no proxy token; the local proxy stays open on loopback.'}
          </Text>
        </Box>
      )
      break
    case 'error':
      body = (
        <Box flexDirection="column" gap={1}>
          <Text color="error">Login failed: {status.message}</Text>
          <Text color="permission">
            Press <Text bold>Enter</Text> to try again, or Esc to cancel.
          </Text>
        </Box>
      )
      break
  }

  return (
    <Dialog
      title="Sign in to JARVIS"
      onCancel={() => onDone('Login cancelled')}
      color="permission"
    >
      {body}
    </Dialog>
  )
}
