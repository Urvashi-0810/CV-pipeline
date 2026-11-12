import { useCallback, useEffect, useState } from 'react'
import { talentApi } from '../../api/talentClient'
import { Alert, Badge, Button, Card, Field, Modal, fieldErrorsOf, formatDateTime } from '../../components/ui'
import { useTalentAuth } from '../TalentAuthContext'
import { useTalentAdmin } from './TalentAdminGate'

// Talent Pool users, modelled on admin/AdminUsersPage.jsx. Talent Pool users have no role field.
const EMPTY = { username: '', display_name: '', initial_password: '' }

function CreateUserModal({ onClose, onCreated }) {
  const { handleError } = useTalentAdmin()
  const [form, setForm] = useState(EMPTY)
  const [error, setError] = useState(null)
  const [busy, setBusy] = useState(false)
  const set = (key) => (e) => setForm({ ...form, [key]: e.target.value })
  const errors = fieldErrorsOf(error)

  async function submit(event) {
    event.preventDefault()
    setBusy(true)
    setError(null)
    try {
      await talentApi('/admin/users', { method: 'POST', body: form })
      onCreated()
    } catch (err) {
      if (!handleError(err)) setError(err)
    } finally {
      setBusy(false)
    }
  }

  return (
    <Modal title="Add Talent Pool user" onClose={onClose}>
      <form onSubmit={submit} className="stack">
        {error && !error.fieldErrors.length && <Alert title={error.title}>{error.message !== error.title ? error.message : null}</Alert>}
        <Field label="Username" error={errors.username} hint="3–50 characters: letters, digits, . _ -">
          {(id) => <input id={id} value={form.username} onChange={set('username')} required autoFocus />}
        </Field>
        <Field label="Name" error={errors.display_name}>
          {(id) => <input id={id} value={form.display_name} onChange={set('display_name')} maxLength={100} required />}
        </Field>
        <Field label="Initial password" error={errors.initial_password} hint="At least 6 characters. Share it with the user privately.">
          {(id) => <input id={id} type="text" autoComplete="off" value={form.initial_password} onChange={set('initial_password')} required />}
        </Field>
        <div className="row-end">
          <Button type="button" variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button type="submit" disabled={busy}>
            {busy ? 'Saving…' : 'Add user'}
          </Button>
        </div>
      </form>
    </Modal>
  )
}

function ResetPasswordModal({ user, onClose }) {
  const { handleError } = useTalentAdmin()
  const [password, setPassword] = useState('')
  const [confirm, setConfirm] = useState('')
  const [error, setError] = useState(null)
  const [done, setDone] = useState(false)
  const [busy, setBusy] = useState(false)
  const mismatch = confirm !== '' && password !== confirm

  async function submit(event) {
    event.preventDefault()
    if (mismatch) return
    setBusy(true)
    setError(null)
    try {
      await talentApi(`/admin/users/${user.id}/password`, { method: 'PUT', body: { new_password: password } })
      setDone(true)
    } catch (err) {
      if (!handleError(err)) setError(err)
    } finally {
      setBusy(false)
    }
  }

  return (
    <Modal title={`Reset password for ${user.display_name}`} onClose={onClose}>
      {done ? (
        <div className="stack">
          <Alert tone="success" title="Password reset.">
            {user.display_name} was logged out of the Talent Pool everywhere and can now log in with the new password.
            Share it with them privately.
          </Alert>
          <div className="row-end">
            <Button onClick={onClose}>Done</Button>
          </div>
        </div>
      ) : (
        <form onSubmit={submit} className="stack">
          {error && !error.fieldErrors.length && <Alert title={error.title}>{error.message !== error.title ? error.message : null}</Alert>}
          <Field label="New password" error={fieldErrorsOf(error).new_password} hint="At least 6 characters.">
            {(id) => <input id={id} type="text" autoComplete="off" value={password} onChange={(e) => setPassword(e.target.value)} required autoFocus />}
          </Field>
          <Field label="Repeat new password" error={mismatch ? 'Does not match.' : null}>
            {(id) => <input id={id} type="text" autoComplete="off" value={confirm} onChange={(e) => setConfirm(e.target.value)} required />}
          </Field>
          <div className="row-end">
            <Button type="button" variant="ghost" onClick={onClose}>
              Cancel
            </Button>
            <Button type="submit" disabled={busy || mismatch || !password}>
              {busy ? 'Saving…' : 'Reset password'}
            </Button>
          </div>
        </form>
      )}
    </Modal>
  )
}

export default function TalentAdminUsersPage() {
  const { me } = useTalentAuth()
  const { handleError } = useTalentAdmin()
  const [users, setUsers] = useState([])
  const [error, setError] = useState(null)
  const [creating, setCreating] = useState(false)
  const [resetting, setResetting] = useState(null)

  const load = useCallback(async () => {
    try {
      setUsers((await talentApi('/admin/users')).items)
    } catch (err) {
      if (!handleError(err)) setError(err)
    }
  }, [handleError])

  useEffect(() => {
    load()
  }, [load])

  async function setActive(user, isActive) {
    setError(null)
    try {
      await talentApi(`/admin/users/${user.id}`, { method: 'PATCH', body: { is_active: isActive } })
      await load()
    } catch (err) {
      if (!handleError(err)) setError(err)
    }
  }

  return (
    <Card title={`Talent Pool users (${users.length})`} actions={<Button onClick={() => setCreating(true)}>+ Add user</Button>}>
      {error && <Alert title={error.title}>{error.message !== error.title ? error.message : null}</Alert>}
      <table className="table">
        <thead>
          <tr>
            <th>Name</th>
            <th>Username</th>
            <th>Status</th>
            <th>Last login</th>
            <th aria-label="Actions" />
          </tr>
        </thead>
        <tbody>
          {users.map((u) => (
            <tr key={u.id} className={u.is_active ? '' : 'row-muted'}>
              <td>
                <strong>{u.display_name}</strong>
                {u.id === me.id && <span className="muted small"> (you)</span>}
              </td>
              <td className="mono">{u.username}</td>
              <td>{u.is_active ? <Badge tone="green">Active</Badge> : <Badge tone="red">Deactivated</Badge>}</td>
              <td className="num">{formatDateTime(u.last_login_at)}</td>
              <td className="row-actions">
                <Button variant="ghost" size="sm" onClick={() => setResetting(u)}>
                  Reset password
                </Button>
                {u.is_active ? (
                  <Button variant="danger" size="sm" disabled={u.id === me.id} onClick={() => setActive(u, false)}>
                    Deactivate
                  </Button>
                ) : (
                  <Button variant="ghost" size="sm" onClick={() => setActive(u, true)}>
                    Reactivate
                  </Button>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      {creating && (
        <CreateUserModal
          onClose={() => setCreating(false)}
          onCreated={() => {
            setCreating(false)
            load()
          }}
        />
      )}
      {resetting && <ResetPasswordModal user={resetting} onClose={() => setResetting(null)} />}
    </Card>
  )
}
