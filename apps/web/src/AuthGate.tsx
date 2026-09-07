import { FormEvent, ReactNode, useEffect, useState } from 'react'

type AuthSession = {
  email: string
  role: string
  organization_id: string
  permissions: string[]
  expires_at: string | null
  auth_enabled: boolean
}

type AuthGateProps = { children: ReactNode }

type AuthStatus = 'loading' | 'ready' | 'signed-out' | 'error'

const ROLE_LABELS: Record<string, string> = {
  platform_owner: 'Platform Owner',
  org_admin: 'Org Admin',
  developer: 'Developer',
  reviewer: 'Reviewer',
  deployment_approver: 'Deployment Approver',
  viewer: 'Viewer',
}

async function readSession(): Promise<AuthSession | null> {
  const response = await fetch('/api/v1/auth/me', {
    credentials: 'same-origin',
    cache: 'no-store',
  })
  if (response.status === 401) return null
  if (!response.ok) throw new Error(`Authentication service unavailable (${response.status})`)
  return response.json() as Promise<AuthSession>
}

export default function AuthGate({ children }: AuthGateProps) {
  const [status, setStatus] = useState<AuthStatus>('loading')
  const [session, setSession] = useState<AuthSession | null>(null)
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [message, setMessage] = useState('')
  const [busy, setBusy] = useState(false)

  async function refreshSession() {
    try {
      const next = await readSession()
      setSession(next)
      setStatus(next ? 'ready' : 'signed-out')
    } catch (error) {
      setMessage(error instanceof Error ? error.message : 'Authentication service unavailable.')
      setStatus('error')
    }
  }

  useEffect(() => {
    refreshSession()
    const timer = window.setInterval(refreshSession, 5 * 60 * 1000)
    return () => window.clearInterval(timer)
  }, [])

  async function signIn(event: FormEvent) {
    event.preventDefault()
    if (!email.trim() || !password) return
    setBusy(true)
    setMessage('')
    try {
      const response = await fetch('/api/v1/auth/login', {
        method: 'POST',
        credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ email: email.trim(), password }),
      })
      const payload = await response.json().catch(() => ({})) as { detail?: string } & Partial<AuthSession>
      if (!response.ok) throw new Error(payload.detail || 'Unable to sign in.')
      setPassword('')
      await refreshSession()
    } catch (error) {
      setPassword('')
      setMessage(error instanceof Error ? error.message : 'Unable to sign in.')
      setStatus('signed-out')
    } finally {
      setBusy(false)
    }
  }

  async function signOut() {
    setBusy(true)
    try {
      await fetch('/api/v1/auth/logout', { method: 'POST', credentials: 'same-origin' })
    } finally {
      setSession(null)
      setPassword('')
      setStatus('signed-out')
      setBusy(false)
    }
  }

  if (status === 'loading') {
    return (
      <main className="auth-shell auth-loading" aria-live="polite">
        <div className="auth-mark">IC</div>
        <strong>Opening Inzozi Code…</strong>
        <span>Checking the private staging session.</span>
      </main>
    )
  }

  if (!session || status === 'signed-out' || status === 'error') {
    return (
      <main className="auth-shell">
        <section className="auth-card">
          <div className="auth-brand">
            <div className="auth-mark">IC</div>
            <div><strong>Inzozi Code</strong><span>Private staging access</span></div>
          </div>
          <div className="auth-copy">
            <span className="auth-eyebrow">AQUILA VENTURE STUDIO</span>
            <h1>Sign in to the guarded workspace.</h1>
            <p>Repository credentials remain server-side. Your role controls which workspace and Git actions are available.</p>
          </div>
          <form onSubmit={signIn} className="auth-form">
            <label>Email<input type="email" value={email} onChange={(event) => setEmail(event.target.value)} autoComplete="username" placeholder="you@inzozidigital.com" required /></label>
            <label>Password<input type="password" value={password} onChange={(event) => setPassword(event.target.value)} autoComplete="current-password" required /></label>
            {message && <div className="auth-message" role="alert">{message}</div>}
            <button type="submit" disabled={busy || !email.trim() || !password}>{busy ? 'Signing in…' : 'Sign in securely'}</button>
          </form>
          <div className="auth-trust">
            <span><strong>HttpOnly session</strong><small>Browser scripts cannot read the session token.</small></span>
            <span><strong>Role enforced</strong><small>Write and Git actions are checked by the API.</small></span>
            <span><strong>Merge disabled</strong><small>Production remains outside this staging gate.</small></span>
          </div>
        </section>
      </main>
    )
  }

  if (!session.auth_enabled) return <>{children}</>

  return (
    <>
      {children}
      <div className="auth-session-chip" aria-label="Signed-in Inzozi Code session">
        <span><strong>{session.email}</strong><small>{ROLE_LABELS[session.role] || session.role}</small></span>
        <button type="button" onClick={signOut} disabled={busy}>Sign out</button>
      </div>
    </>
  )
}
