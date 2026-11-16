import { useCallback, useEffect, useState } from 'react'
import { talentApi } from '../../api/talentClient'
import { Alert, Button, Card, ConfirmModal, Field, Modal, fieldErrorsOf, formatDateTime } from '../../components/ui'
import { useTalentAdmin } from './TalentAdminGate'

// The mailboxes CVs are fetched from (Zoho, over IMAP with an app password). Talent Pool admin only.
// The app password is stored encrypted and never shown; leaving it blank when editing keeps it.
const EMPTY = { name: '', email: '', password: '', host: 'imap.zoho.in', port: 993, default_folder: 'INBOX' }

function MailboxModal({ mailbox, onClose, onSaved }) {
  const { handleError } = useTalentAdmin()
  const [form, setForm] = useState(mailbox ? { ...mailbox, password: '' } : EMPTY)
  const [error, setError] = useState(null)
  const [busy, setBusy] = useState(false)
  const set = (key) => (e) => setForm({ ...form, [key]: e.target.value })
  const errors = fieldErrorsOf(error)

  async function submit(event) {
    event.preventDefault()
    setBusy(true)
    setError(null)
    const body = { name: form.name, email: form.email, host: form.host, port: Number(form.port),
      default_folder: form.default_folder, password: form.password }
    try {
      await talentApi(mailbox ? `/mailboxes/${mailbox.id}` : '/mailboxes', { method: mailbox ? 'PATCH' : 'POST', body })
      onSaved()
    } catch (err) {
      if (!handleError(err)) setError(err)
    } finally {
      setBusy(false)
    }
  }

  return (
    <Modal title={mailbox ? `Edit ${mailbox.name}` : 'Add a mailbox'} onClose={onClose}>
      <form onSubmit={submit} className="stack">
        {error && !error.fieldErrors.length && <Alert title={error.title}>{error.message !== error.title ? error.message : null}</Alert>}
        <Field label="Name" error={errors.name} hint="How it's shown when choosing a mailbox, e.g. Recruitment">
          {(id) => <input id={id} value={form.name} onChange={set('name')} autoFocus />}
        </Field>
        <Field label="Email address" error={errors.email}>
          {(id) => <input id={id} type="email" value={form.email} onChange={set('email')} required />}
        </Field>
        <Field label="App password" error={errors.password}
          hint={mailbox ? 'Leave blank to keep the current one.' : 'Zoho: Settings → Security → App passwords. Not your normal login password.'}>
          {(id) => <input id={id} type="password" autoComplete="new-password" value={form.password} onChange={set('password')}
            required={!mailbox} />}
        </Field>
        <div className="talent-form-grid">
          <Field label="Mail server" error={errors.host} hint="Zoho India: imap.zoho.in">
            {(id) => <input id={id} value={form.host} onChange={set('host')} required />}
          </Field>
          <Field label="Port" error={errors.port}>
            {(id) => <input id={id} type="number" min="1" max="65535" value={form.port} onChange={set('port')} required />}
          </Field>
        </div>
        <Field label="Usual folder" error={errors.default_folder} hint="Chosen first when starting a fetch. Any folder can be picked then.">
          {(id) => <input id={id} value={form.default_folder} onChange={set('default_folder')} required />}
        </Field>
        <div className="row-end">
          <Button type="button" variant="ghost" onClick={onClose}>Cancel</Button>
          <Button type="submit" disabled={busy}>{busy ? 'Saving…' : mailbox ? 'Save' : 'Add mailbox'}</Button>
        </div>
      </form>
    </Modal>
  )
}

export default function TalentAdminMailboxesPage() {
  const { handleError } = useTalentAdmin()
  const [items, setItems] = useState([])
  const [error, setError] = useState(null)
  const [editing, setEditing] = useState(null)        // null, 'new' or a mailbox
  const [removing, setRemoving] = useState(null)
  const [tests, setTests] = useState({})
  const [busy, setBusy] = useState(false)

  const load = useCallback(async () => {
    try {
      setItems((await talentApi('/mailboxes')).items)
    } catch (err) {
      if (!handleError(err)) setError(err)
    }
  }, [handleError])

  useEffect(() => {
    load()
  }, [load])

  async function test(mailbox) {
    setTests((t) => ({ ...t, [mailbox.id]: { running: true } }))
    try {
      const result = await talentApi(`/mailboxes/${mailbox.id}/test`, { method: 'POST' })
      setTests((t) => ({ ...t, [mailbox.id]: result }))
    } catch (err) {
      if (!handleError(err)) setTests((t) => ({ ...t, [mailbox.id]: { ok: false, message: err.message } }))
    }
  }

  async function remove() {
    setBusy(true)
    try {
      await talentApi(`/mailboxes/${removing.id}`, { method: 'DELETE' })
      setRemoving(null)
      await load()
    } catch (err) {
      if (!handleError(err)) setError(err)
    } finally {
      setBusy(false)
    }
  }

  return (
    <Card title={`Mailboxes (${items.length})`} actions={<Button onClick={() => setEditing('new')}>+ Add mailbox</Button>}>
      <p className="muted small">
        CVs are fetched from these mailboxes. On the Pipeline page, each fetch reads one mailbox and one of its folders.
        The subject and sender filters on the Settings tab apply to all of them.
      </p>
      {error && <Alert title={error.title}>{error.message !== error.title ? error.message : null}</Alert>}
      {items.length === 0 ? (
        <p className="muted">No mailboxes yet. Add the first one.</p>
      ) : (
        <div className="table-wrap">
          <table className="table">
            <thead>
              <tr>
                <th>Name</th>
                <th>Email</th>
                <th>Server</th>
                <th>Usual folder</th>
                <th>Last fetch</th>
                <th aria-label="Actions" />
              </tr>
            </thead>
            <tbody>
              {items.map((m) => {
                const fetches = Object.entries(m.last_fetch || {}).sort((a, b) => (b[1] || '').localeCompare(a[1] || ''))
                const result = tests[m.id]
                return (
                  <tr key={m.id}>
                    <td>
                      <strong>{m.name}</strong>
                      {result && !result.running && (
                        <div className={`small ${result.ok ? 'talent-test-ok' : 'talent-test-bad'}`}>{result.message}</div>
                      )}
                    </td>
                    <td className="mono">{m.email}</td>
                    <td className="mono">{m.host}:{m.port}</td>
                    <td>{m.default_folder}</td>
                    <td className="small">
                      {fetches.length ? fetches.slice(0, 2).map(([folder, at]) => (
                        <div key={folder}>{folder}: {formatDateTime(at)}</div>
                      )) : <span className="muted">Never</span>}
                    </td>
                    <td className="row-actions">
                      <Button variant="ghost" size="sm" disabled={result?.running} onClick={() => test(m)}>
                        {result?.running ? 'Testing…' : 'Test'}
                      </Button>
                      <Button variant="ghost" size="sm" onClick={() => setEditing(m)}>Edit</Button>
                      <Button variant="danger" size="sm" onClick={() => setRemoving(m)}>Remove</Button>
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      )}
      {editing && (
        <MailboxModal mailbox={editing === 'new' ? null : editing} onClose={() => setEditing(null)}
          onSaved={() => {
            setEditing(null)
            load()
          }} />
      )}
      {removing && (
        <ConfirmModal title={`Remove ${removing.name}?`} danger confirmLabel="Remove mailbox" busy={busy}
          onConfirm={remove} onClose={() => setRemoving(null)}>
          <p>The connection to <strong>{removing.email}</strong> is removed, so it can't be fetched any more.</p>
          <p className="muted small">Candidates and CVs that came from it stay in the Talent Pool.</p>
        </ConfirmModal>
      )}
    </Card>
  )
}
