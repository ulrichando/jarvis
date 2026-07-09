import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import { render, screen, fireEvent, waitFor, cleanup } from '@testing-library/react'

// The login page's 2FA challenge step: better-auth answers a correct
// email+password for an enrolled user with `{ twoFactorRedirect: true }` and
// NO session. Regression guard for the lockout bug where the page treated
// that as success and router.push()ed into the auth-gate redirect loop.

const pushMock = vi.fn()
vi.mock('next/navigation', () => ({
  useRouter: () => ({ push: pushMock }),
  useSearchParams: () => new URLSearchParams(),
}))

const signInEmail = vi.fn()
const verifyTotp = vi.fn()
const verifyBackupCode = vi.fn()
vi.mock('@/lib/auth-client', () => ({
  signIn: { email: (...args: unknown[]) => signInEmail(...args) },
  authClient: {
    twoFactor: {
      verifyTotp: (...args: unknown[]) => verifyTotp(...args),
      verifyBackupCode: (...args: unknown[]) => verifyBackupCode(...args),
    },
  },
}))

import LoginPage from '@/app/(auth)/login/page'

const ok = (data: unknown) => ({ data, error: null })
const err = (code: string, message: string) => ({
  data: null,
  error: { code, message, status: 401 },
})

async function fillCredentialsAndSubmit() {
  fireEvent.change(screen.getByPlaceholderText('you@example.com'), {
    target: { value: 'u@example.com' },
  })
  fireEvent.change(screen.getByPlaceholderText('••••••••'), {
    target: { value: 'hunter22' },
  })
  fireEvent.click(screen.getByText('Sign in', { selector: 'button' }))
}

beforeEach(() => {
  pushMock.mockReset()
  signInEmail.mockReset()
  verifyTotp.mockReset()
  verifyBackupCode.mockReset()
})

afterEach(() => cleanup())

describe('LoginPage two-factor challenge', () => {
  it('signs a non-2FA user straight in (no challenge step)', async () => {
    signInEmail.mockResolvedValue(ok({ token: 'sess', user: { id: 'u1' } }))
    render(<LoginPage />)
    await fillCredentialsAndSubmit()
    await waitFor(() => expect(pushMock).toHaveBeenCalledWith('/chat'))
    expect(screen.queryByText('Two-factor authentication')).toBeNull()
  })

  it('shows the challenge instead of redirecting when signIn returns twoFactorRedirect', async () => {
    signInEmail.mockResolvedValue(ok({ twoFactorRedirect: true, twoFactorMethods: ['totp'] }))
    render(<LoginPage />)
    await fillCredentialsAndSubmit()
    await waitFor(() => expect(screen.getByText('Two-factor authentication')).toBeTruthy())
    // The lockout bug: this used to fire and loop back to /login.
    expect(pushMock).not.toHaveBeenCalled()
  })

  it('verifies a TOTP code and routes to the app', async () => {
    signInEmail.mockResolvedValue(ok({ twoFactorRedirect: true }))
    verifyTotp.mockResolvedValue(ok({ token: 'sess', user: { id: 'u1' } }))
    render(<LoginPage />)
    await fillCredentialsAndSubmit()
    await waitFor(() => expect(screen.getByPlaceholderText('000000')).toBeTruthy())

    fireEvent.change(screen.getByPlaceholderText('000000'), { target: { value: '123456' } })
    fireEvent.click(screen.getByText('Verify', { selector: 'button' }))

    await waitFor(() => expect(pushMock).toHaveBeenCalledWith('/chat'))
    expect(verifyTotp).toHaveBeenCalledWith({ code: '123456', trustDevice: false })
  })

  it('shows a clear error on a wrong TOTP code and stays on the challenge', async () => {
    signInEmail.mockResolvedValue(ok({ twoFactorRedirect: true }))
    verifyTotp.mockResolvedValue(err('INVALID_CODE', 'Invalid code'))
    render(<LoginPage />)
    await fillCredentialsAndSubmit()
    await waitFor(() => expect(screen.getByPlaceholderText('000000')).toBeTruthy())

    fireEvent.change(screen.getByPlaceholderText('000000'), { target: { value: '000001' } })
    fireEvent.click(screen.getByText('Verify', { selector: 'button' }))

    await waitFor(() => expect(screen.getByText(/That code didn't work/)).toBeTruthy())
    expect(pushMock).not.toHaveBeenCalled()
    // Still on the challenge, input cleared for retry.
    expect((screen.getByPlaceholderText('000000') as HTMLInputElement).value).toBe('')
  })

  it('falls back to a backup code and passes trustDevice through', async () => {
    signInEmail.mockResolvedValue(ok({ twoFactorRedirect: true }))
    verifyBackupCode.mockResolvedValue(ok({ user: { id: 'u1' } }))
    render(<LoginPage />)
    await fillCredentialsAndSubmit()
    await waitFor(() => expect(screen.getByText('Use a backup code')).toBeTruthy())

    fireEvent.click(screen.getByText('Use a backup code'))
    fireEvent.change(screen.getByPlaceholderText('Backup code'), {
      target: { value: 'abcde-12345' },
    })
    fireEvent.click(screen.getByLabelText(/Trust this device/))
    fireEvent.click(screen.getByText('Verify', { selector: 'button' }))

    await waitFor(() => expect(pushMock).toHaveBeenCalledWith('/chat'))
    expect(verifyBackupCode).toHaveBeenCalledWith({ code: 'abcde-12345', trustDevice: true })
    expect(verifyTotp).not.toHaveBeenCalled()
  })

  it('returns to the credentials step when the 2FA challenge window expired', async () => {
    signInEmail.mockResolvedValue(ok({ twoFactorRedirect: true }))
    verifyTotp.mockResolvedValue(err('INVALID_TWO_FACTOR_COOKIE', 'Invalid two factor cookie'))
    render(<LoginPage />)
    await fillCredentialsAndSubmit()
    await waitFor(() => expect(screen.getByPlaceholderText('000000')).toBeTruthy())

    fireEvent.change(screen.getByPlaceholderText('000000'), { target: { value: '123456' } })
    fireEvent.click(screen.getByText('Verify', { selector: 'button' }))

    await waitFor(() => expect(screen.getByText(/verification window expired/)).toBeTruthy())
    expect(screen.getByPlaceholderText('you@example.com')).toBeTruthy()
    expect(pushMock).not.toHaveBeenCalled()
  })

  it('"Back to sign in" leaves the challenge and clears the password', async () => {
    signInEmail.mockResolvedValue(ok({ twoFactorRedirect: true }))
    render(<LoginPage />)
    await fillCredentialsAndSubmit()
    await waitFor(() => expect(screen.getByText('Back to sign in')).toBeTruthy())

    fireEvent.click(screen.getByText('Back to sign in'))
    expect(screen.getByText('Welcome back')).toBeTruthy()
    expect((screen.getByPlaceholderText('••••••••') as HTMLInputElement).value).toBe('')
  })
})
