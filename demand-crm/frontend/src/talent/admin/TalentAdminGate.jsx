import { createContext, useCallback, useContext, useEffect, useState } from 'react'
import { NavLink, Outlet } from 'react-router-dom'
import { talentApi } from '../../api/talentClient'
import { Alert, Button, Card, Field, formatDateTime } from '../../components/ui'
import { useTalentAuth } from '../TalentAuthContext'

// The Talent Pool admin area, modelled on admin/AdminGate.jsx. Its password is separate from the CRM's.
const TalentAdminContext = createContext(null)

// Admin pages pass any API error here: if the unlock expired, the password prompt comes back.
export function useTalentAdmin() {
  return useContext(TalentAdminContext)
}

export default function TalentAdminGate() {
  const { refresh } = useTalentAuth()
  const [status, setStatus] = useState(null)
  const [password, setPassword] = useState('')
  const [error, setError] = useState(null)
  const [busy, setBusy] = useState(false)

  const load = useCallback(async () => {
    setStatus(await talentApi('/admin/session'))
  }, [])

  useEffect(() => {
    load().catch(setError)
  }, [load])

  const handleError = useCallback((err) => {
    if (err?.is?.('admin-session-required')) {
      setStatus((s) => ({ ...s, active: false, expires_at: null }))
      return true
    }
    return false
  }, [])

  async function unlock(event) {
    event.preventDefault()
    setBusy(true)
    setError(null)
    try {
      await talentApi('/admin/session', { method: 'POST', body: { admin_password: password } })
      setPassword('')
      await Promise.all([load(), refresh()])
    } catch (err) {
      setError(err)
    } finally {
      setBusy(false)
    }
  }

  async function lock() {
    await talentApi('/admin/session', { method: 'DELETE' })
    await Promise.all([load(), refresh()])
  }

  if (!status) {
    return (
      <div className="stack">
        <h1 className="page-title">Talent Pool Admin</h1>
        {error ? <Alert title={error.title}>{error.message}</Alert> : <p className="muted">Loading…</p>}
      </div>
    )
  }

  if (!status.active) {
    return (
      <div className="stack">
        <h1 className="page-title">Talent Pool Admin</h1>
      <div className="narrow">
        <Card title="Enter the Talent Pool admin password">
          {!status.password_configured ? (
            <Alert title="The Talent Pool admin password has not been set up yet.">
              Run bootstrap_auth.py in the cv_pipeline backend (see its README).
            </Alert>
          ) : (
            <form onSubmit={unlock} className="stack">
              <p className="muted small">
                Talent Pool admin needs its own password, separate from the CRM admin password.
              </p>
              <Alert title={error?.title}>{error?.message !== error?.title ? error?.message : null}</Alert>
              <Field label="Talent Pool admin password">
                {(id) => (
                  <input id={id} type="password" autoComplete="off" value={password}
                    onChange={(e) => setPassword(e.target.value)} required autoFocus />
                )}
              </Field>
              <div>
                <Button type="submit" disabled={busy || !password}>
                  {busy ? 'Checking…' : 'Open Talent Pool admin'}
                </Button>
              </div>
            </form>
          )}
        </Card>
      </div>
      </div>
    )
  }

  return (
    <TalentAdminContext.Provider value={{ handleError }}>
      <div className="stack">
        <div className="page-header">
          <h1 className="page-title">Talent Pool Admin</h1>
          <div className="page-actions">
            <span className="muted small">Unlocked until {formatDateTime(status.expires_at)}</span>
            <Button variant="ghost" size="sm" onClick={lock}>
              Lock admin
            </Button>
          </div>
        </div>
        <nav className="tabs" aria-label="Talent Pool admin sections">
          <NavLink to="/talent/admin/users" className="tab">
            Users
          </NavLink>
          <NavLink to="/talent/admin/settings" className="tab">
            Settings
          </NavLink>
          <NavLink to="/talent/admin/password" className="tab">
            Admin password
          </NavLink>
        </nav>
        <Outlet />
      </div>
    </TalentAdminContext.Provider>
  )
}
