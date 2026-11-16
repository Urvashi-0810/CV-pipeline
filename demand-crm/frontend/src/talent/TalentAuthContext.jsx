import { createContext, useCallback, useContext, useEffect, useState } from 'react'
import { onTalentUnauthorized, setTalentCsrfToken, talentApi } from '../api/talentClient'

// The Talent Pool login, separate from the CRM login (modelled on auth/AuthContext.jsx).
const TalentAuthContext = createContext(null)

export function TalentAuthProvider({ children }) {
  // undefined = still checking, null = logged out, object = logged in
  const [me, setMe] = useState(undefined)
  const [error, setError] = useState(null)

  const refresh = useCallback(async () => {
    try {
      const data = await talentApi('/me')
      setTalentCsrfToken(data.csrf_token)
      setMe(data)
      setError(null)
      return data
    } catch (err) {
      setTalentCsrfToken(null)
      setMe(null)
      // A 401 just means "not logged in". Anything else (server down, no database) is shown.
      if (err.status !== 401) setError(err)
      return null
    }
  }, [])

  useEffect(() => {
    refresh()
    return onTalentUnauthorized(() => {
      setTalentCsrfToken(null)
      setMe(null)
    })
  }, [refresh])

  const login = useCallback(
    async (username, password) => {
      const data = await talentApi('/auth/login', { method: 'POST', body: { username, password } })
      setTalentCsrfToken(data.csrf_token)
      return refresh()
    },
    [refresh],
  )

  const logout = useCallback(async () => {
    try {
      await talentApi('/auth/logout', { method: 'POST' })
    } finally {
      setTalentCsrfToken(null)
      setMe(null)
    }
  }, [])

  return (
    <TalentAuthContext.Provider value={{ me, error, refresh, login, logout }}>{children}</TalentAuthContext.Provider>
  )
}

export function useTalentAuth() {
  return useContext(TalentAuthContext)
}
