import { useState, type FormEvent } from 'react'
import { ApiError, api } from '../api/client'
import { useAuth } from '../auth'

// Synthetic demo accounts from data/seed/people.json.
const DEMO_ACCOUNTS = [
  { username: 'patient1', password: 'Patient1@demo', label: 'Arjun Mehta', note: 'Knee surgery planned · Gold plan' },
  { username: 'patient3', password: 'Patient3@demo', label: 'Rahul Verma', note: 'Knee surgery · Basic plan' },
  { username: 'admin1', password: 'Admin1@demo', label: 'Hospital admin', note: 'Documents and audit trail' },
]

export function Login() {
  const { signIn } = useAuth()
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  async function login(user: string, pass: string) {
    setBusy(true)
    setError(null)
    try {
      signIn(await api.login({ username: user, password: pass }))
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Sign-in failed')
    } finally {
      setBusy(false)
    }
  }

  const onSubmit = (e: FormEvent) => {
    e.preventDefault()
    void login(username, password)
  }

  return (
    <main className="login">
      <div className="login-card">
        <div className="brand brand-large">
          <span className="brand-mark" aria-hidden>
            +
          </span>
          <div>
            <div className="brand-name">Patient Services Assistant</div>
            <div className="brand-sub">Demo City Hospital</div>
          </div>
        </div>

        <form className="form" onSubmit={onSubmit}>
          <label className="field field-wide">
            <span>Username</span>
            <input value={username} onChange={(e) => setUsername(e.target.value)} autoComplete="username" required />
          </label>
          <label className="field field-wide">
            <span>Password</span>
            <input type="password" value={password} onChange={(e) => setPassword(e.target.value)} autoComplete="current-password" required />
          </label>
          <div className="field-wide">
            <button type="submit" className="btn btn-primary btn-block" disabled={busy}>
              {busy ? 'Signing in…' : 'Sign in'}
            </button>
          </div>
        </form>
        {error && <p className="alert alert-error">{error}</p>}

        <div className="demo-accounts">
          <div className="muted small">Demo accounts (synthetic data)</div>
          {DEMO_ACCOUNTS.map((a) => (
            <button key={a.username} type="button" className="demo-account" disabled={busy} onClick={() => void login(a.username, a.password)}>
              <span className="demo-name">{a.label}</span>
              <span className="muted small">{a.note}</span>
            </button>
          ))}
        </div>
      </div>
    </main>
  )
}
