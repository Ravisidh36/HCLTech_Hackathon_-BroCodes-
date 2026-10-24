import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from 'react'
import { setUnauthorizedHandler } from './api/client'
import type { TokenResponse } from './api/types'

const STORAGE_KEY = 'hospital-assistant-session'

interface AuthState {
  session: TokenResponse | null
  signIn: (session: TokenResponse) => void
  signOut: () => void
}

const AuthContext = createContext<AuthState | null>(null)

function readStored(): TokenResponse | null {
  try {
    const raw = sessionStorage.getItem(STORAGE_KEY)
    return raw ? (JSON.parse(raw) as TokenResponse) : null
  } catch {
    return null
  }
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const [session, setSession] = useState<TokenResponse | null>(readStored)

  const signIn = useCallback((next: TokenResponse) => {
    setSession(next)
    try {
      sessionStorage.setItem(STORAGE_KEY, JSON.stringify(next))
    } catch {
      /* storage unavailable: session lasts for this page only */
    }
  }, [])

  const signOut = useCallback(() => {
    setSession(null)
    try {
      sessionStorage.removeItem(STORAGE_KEY)
    } catch {
      /* ignore */
    }
  }, [])

  useEffect(() => {
    setUnauthorizedHandler(signOut)
  }, [signOut])

  const value = useMemo(() => ({ session, signIn, signOut }), [session, signIn, signOut])
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}

export function useAuth(): AuthState {
  const ctx = useContext(AuthContext)
  if (!ctx) throw new Error('useAuth must be used inside AuthProvider')
  return ctx
}
