import { useState } from 'react'
import { Outlet } from 'react-router-dom'
import { Alert, Button, Card, Field } from '../components/ui'
import { TalentAuthProvider, useTalentAuth } from './TalentAuthContext'

// Every Talent Pool screen sits behind this: it shows the Talent Pool login until the user signs in.
export default function TalentGate() {
  return (
    <TalentAuthProvider>
      <TalentArea />
    </TalentAuthProvider>
  )
}

function TalentArea() {
  const { me, error, refresh } = useTalentAuth()

  if (me === undefined) return <div className="page"><p className="muted">Loading…</p></div>
  if (me === null) {
    return (
      <div className="page">
        <h1 className="page-title">Talent Pool</h1>
        {error ? (
          <div className="narrow stack">
            <Alert title={error.title}>{error.message !== error.title ? error.message : null}</Alert>
            <div>
              <Button variant="ghost" onClick={refresh}>Try again</Button>
            </div>
          </div>
        ) : (
          <TalentLogin />
        )}
      </div>
    )
  }

  // Each Talent Pool page (Candidate List, Pipeline, My Profile, Admin) has its own sidebar link and title.
  return (
    <div className="page">
      <Outlet />
    </div>
  )
}

function TalentLogin() {
  const { login } = useTalentAuth()
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState(null)
  const [busy, setBusy] = useState(false)

  async function submit(event) {
    event.preventDefault()
    setBusy(true)
    setError(null)
    try {
      await login(username, password)
    } catch (err) {
      setError(err)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="narrow">
      <Card title="Sign in to the Talent Pool">
        <form onSubmit={submit} className="stack">
          <p className="muted small">The Talent Pool has its own login, separate from the CRM.</p>
          <Alert title={error?.title}>{error?.message !== error?.title ? error?.message : null}</Alert>
          <Field label="Username">
            {(id) => (
              <input id={id} autoComplete="username" value={username}
                onChange={(e) => setUsername(e.target.value)} required autoFocus />
            )}
          </Field>
          <Field label="Password">
            {(id) => (
              <input id={id} type="password" autoComplete="current-password" value={password}
                onChange={(e) => setPassword(e.target.value)} required />
            )}
          </Field>
          <div>
            <Button type="submit" disabled={busy}>
              {busy ? 'Signing in…' : 'Sign in'}
            </Button>
          </div>
          <p className="muted small">Accounts are created by the Talent Pool admin.</p>
        </form>
      </Card>
    </div>
  )
}
