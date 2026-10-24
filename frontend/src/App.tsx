import { AuthProvider, useAuth } from './auth'
import { Logo } from './components/Logo'
import { AdminConsole } from './pages/AdminConsole'
import { Login } from './pages/Login'
import { PatientChat } from './pages/PatientChat'

function Shell() {
  const { session, signOut } = useAuth()
  if (!session) return <Login />

  const isAdmin = session.role === 'admin'
  return (
    <div className="app">
      <header className="topbar">
        <div className="brand">
          <Logo size={34} />
          <div>
            <div className="brand-name">Nirog</div>
            <div className="brand-sub">
              {isAdmin ? 'Administration' : 'Patient Services Assistant'} · Demo City Hospital
            </div>
          </div>
        </div>
        <div className="user">
          <div className="user-text">
            <div className="user-name">{session.full_name}</div>
            <div className="muted small">{isAdmin ? 'Administrator' : `Patient ${session.patient_id}`}</div>
          </div>
          <button type="button" className="btn btn-small" onClick={signOut}>
            Sign out
          </button>
        </div>
      </header>
      <main className="main">{isAdmin ? <AdminConsole /> : <PatientChat />}</main>
    </div>
  )
}

export default function App() {
  return (
    <AuthProvider>
      <Shell />
    </AuthProvider>
  )
}
