import { useState } from 'react'
import { talentApi } from '../api/talentClient'
import { Alert, Button, Card, Field, fieldErrorsOf } from '../components/ui'

// Any Talent Pool user changes their own Talent Pool password (not the CRM one).
export default function TalentMyPasswordPage() {
  const [current, setCurrent] = useState('')
  const [next, setNext] = useState('')
  const [confirm, setConfirm] = useState('')
  const [error, setError] = useState(null)
  const [done, setDone] = useState(false)
  const [busy, setBusy] = useState(false)

  const mismatch = confirm && next !== confirm
  const errors = fieldErrorsOf(error)

  async function submit(event) {
    event.preventDefault()
    if (mismatch) return
    setBusy(true)
    setError(null)
    setDone(false)
    try {
      await talentApi('/me/password', { method: 'PUT', body: { current_password: current, new_password: next } })
      setCurrent('')
      setNext('')
      setConfirm('')
      setDone(true)
    } catch (err) {
      setError(err)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="narrow">
      <Card title="Change my Talent Pool password">
        <form onSubmit={submit} className="stack">
          {done && (
            <Alert tone="success">Password changed. You were logged out of the Talent Pool in your other browsers.</Alert>
          )}
          {error && !error.fieldErrors.length && <Alert title={error.title}>{error.message !== error.title ? error.message : null}</Alert>}
          <Field label="Current password">
            {(id) => <input id={id} type="password" autoComplete="current-password" value={current} onChange={(e) => setCurrent(e.target.value)} required />}
          </Field>
          <Field label="New password" error={errors.new_password} hint="At least 6 characters.">
            {(id) => <input id={id} type="password" autoComplete="new-password" value={next} onChange={(e) => setNext(e.target.value)} required />}
          </Field>
          <Field label="Repeat new password" error={mismatch ? 'Does not match.' : null}>
            {(id) => <input id={id} type="password" autoComplete="new-password" value={confirm} onChange={(e) => setConfirm(e.target.value)} required />}
          </Field>
          <div>
            <Button type="submit" disabled={busy || mismatch}>
              {busy ? 'Saving…' : 'Change password'}
            </Button>
          </div>
        </form>
      </Card>
    </div>
  )
}
