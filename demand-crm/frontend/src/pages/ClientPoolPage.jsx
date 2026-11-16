import { useCallback, useEffect, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { api } from '../api/client'
import { EditIcon, SearchIcon, TrashIcon } from '../components/icons'
import {
  Alert, Button, ConfirmModal, Drawer, ErrorAlert, Field, Pagination, fieldErrorsOf, useDebounced,
} from '../components/ui'

// Client Pool: the companies that send us demand inquiries, and the people to contact there.
const DEFAULT_LIMIT = 25
// Same simple check as the server: something@domain.tld, no spaces.
const EMAIL_RE = /^[^@\s]+@[^@\s.]+(\.[^@\s.]+)+$/
const EMPTY_CONTACT = { name: '', phone: '', email: '' }

function toForm(client) {
  if (!client) return { name: '', email: '', contacts: [{ ...EMPTY_CONTACT }] }
  return {
    name: client.name,
    email: client.email || '',
    contacts: client.contacts.map((c) => ({ name: c.name, phone: c.phone || '', email: c.email || '' })),
  }
}

// Checked in the browser for quick feedback; the server checks the same again.
function validate(form) {
  const errors = {}
  if (!form.name.trim()) errors.name = 'Enter the company name.'
  if (form.email.trim() && !EMAIL_RE.test(form.email.trim())) errors.email = 'Enter a valid email address, like name@company.com.'
  form.contacts.forEach((c, i) => {
    if (!c.name.trim()) errors[`contacts.${i}.name`] = "Enter the contact person's name."
    if (c.email.trim() && !EMAIL_RE.test(c.email.trim())) errors[`contacts.${i}.email`] = 'Enter a valid email address.'
  })
  return errors
}

function ClientDrawer({ client, onClose, onSaved }) {
  const [form, setForm] = useState(() => toForm(client))
  const [checked, setChecked] = useState(false)          // show browser-side errors after the first Save
  const [error, setError] = useState(null)
  const [busy, setBusy] = useState(false)
  const errors = { ...(checked ? validate(form) : {}), ...fieldErrorsOf(error) }

  const setField = (key) => (e) => setForm({ ...form, [key]: e.target.value })
  const setContact = (i, key) => (e) =>
    setForm({ ...form, contacts: form.contacts.map((c, j) => (j === i ? { ...c, [key]: e.target.value } : c)) })

  async function save() {
    setChecked(true)
    setError(null)
    if (Object.keys(validate(form)).length) return
    setBusy(true)
    try {
      const body = {
        name: form.name,
        email: form.email,
        // A contact row left completely empty is simply dropped.
        contacts: form.contacts.filter((c) => c.name.trim() || c.phone.trim() || c.email.trim()),
      }
      const saved = await api(client ? `/clients/${client.id}` : '/clients', { method: client ? 'PUT' : 'POST', body })
      onSaved(saved)
    } catch (err) {
      setError(err)
    } finally {
      setBusy(false)
    }
  }

  return (
    <Drawer
      title={client ? client.name : 'Add client'}
      subtitle={client ? 'Edit the client and its contact persons' : 'A company that sends us demand inquiries'}
      onClose={onClose}
      footer={
        <div className="row-end client-drawer-actions">
          <Button variant="ghost" onClick={onClose} disabled={busy}>
            Cancel
          </Button>
          <Button onClick={save} disabled={busy}>
            {busy ? 'Saving…' : client ? 'Save changes' : 'Add client'}
          </Button>
        </div>
      }
    >
      <form className="stack" onSubmit={(e) => { e.preventDefault(); save() }} noValidate>
        {error && !error.fieldErrors.length && <ErrorAlert error={error} />}
        <Field label="Client / company name" error={errors.name}>
          {(id) => <input id={id} value={form.name} onChange={setField('name')} maxLength={200} autoFocus required />}
        </Field>
        <Field label="Client email address" error={errors.email}>
          {(id) => <input id={id} type="email" value={form.email} onChange={setField('email')} maxLength={254} placeholder="" />}
        </Field>

        <div className="stack-sm">
          <h3 className="client-section-title">Contact persons</h3>
          {form.contacts.length === 0 && <p className="muted small">No contact persons yet.</p>}
          {form.contacts.map((c, i) => (
            <fieldset key={i} className="client-contact-box">
              <legend className="sr-only">Contact person {i + 1}</legend>
              <div className="client-contact-head">
                <strong className="small">Contact person {i + 1}</strong>
                <button type="button" className="link-btn" onClick={() => setForm({ ...form, contacts: form.contacts.filter((_, j) => j !== i) })}>
                  Remove
                </button>
              </div>
              <Field label="Name" error={errors[`contacts.${i}.name`]}>
                {(id) => <input id={id} value={c.name} onChange={setContact(i, 'name')} maxLength={100} />}
              </Field>
              <div className="client-contact-grid">
                <Field label="Phone number" error={errors[`contacts.${i}.phone`]}>
                  {(id) => <input id={id} type="tel" value={c.phone} onChange={setContact(i, 'phone')} maxLength={30} placeholder="+971 50 123 4567" />}
                </Field>
                <Field label="Email address" error={errors[`contacts.${i}.email`]}>
                  {(id) => <input id={id} type="email" value={c.email} onChange={setContact(i, 'email')} maxLength={254} />}
                </Field>
              </div>
            </fieldset>
          ))}
          <div>
            <Button type="button" variant="ghost" size="sm" disabled={form.contacts.length >= 50}
              onClick={() => setForm({ ...form, contacts: [...form.contacts, { ...EMPTY_CONTACT }] })}>
              + Add contact person
            </Button>
          </div>
        </div>
        <button type="submit" hidden aria-hidden="true" tabIndex={-1} />
      </form>
    </Drawer>
  )
}

const dash = <span className="muted">—</span>

export default function ClientPoolPage() {
  // Search and page live in the URL, so refresh/back keep them.
  const [params, setParams] = useSearchParams()
  const [searchText, setSearchText] = useState(params.get('q') || '')
  const debouncedSearch = useDebounced(searchText, 300)
  const [page, setPage] = useState({ items: [], total: 0, limit: DEFAULT_LIMIT, offset: 0 })
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [editing, setEditing] = useState(null)            // null, 'new' or a client
  const [deleting, setDeleting] = useState(null)
  const [busy, setBusy] = useState(false)
  const [notice, setNotice] = useState(null)

  const q = params.get('q') || ''
  const limit = Number(params.get('limit')) || DEFAULT_LIMIT
  const offset = Number(params.get('offset')) || 0

  function updateParams(changes, { resetPage = true } = {}) {
    const next = new URLSearchParams(params)
    for (const [k, v] of Object.entries(changes)) {
      if (v === '' || v == null) next.delete(k)
      else next.set(k, String(v))
    }
    if (resetPage) next.delete('offset')
    setParams(next, { replace: true })
  }

  useEffect(() => {
    if (q !== debouncedSearch) updateParams({ q: debouncedSearch })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [debouncedSearch])

  const load = useCallback(async () => {
    const query = new URLSearchParams({ limit, offset })
    if (q) query.set('q', q)
    setPage(await api(`/clients?${query}`))
  }, [q, limit, offset])

  useEffect(() => {
    setLoading(true)
    load()
      .then(() => setError(null))
      .catch(setError)
      .finally(() => setLoading(false))
  }, [load])

  async function remove() {
    setBusy(true)
    try {
      await api(`/clients/${deleting.id}`, { method: 'DELETE' })
      setNotice(`${deleting.name} was deleted.`)
      setDeleting(null)
      // Deleting the last row of a page goes back one page.
      if (page.items.length === 1 && offset > 0) updateParams({ offset: Math.max(0, offset - limit) || '' }, { resetPage: false })
      else await load()
    } catch (err) {
      setError(err)
      setDeleting(null)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="page">
      <div className="page-header">
        <h1 className="page-title">Client Pool</h1>
        <Button onClick={() => { setNotice(null); setEditing('new') }}>+ Add client</Button>
      </div>

      <ErrorAlert error={error} />
      {notice && <Alert tone="success" title={notice} />}

      <section className="card filters-card">
        <div className="search-box">
          <SearchIcon />
          <input
            type="search"
            placeholder="Search by company name or contact person…"
            value={searchText}
            onChange={(e) => setSearchText(e.target.value)}
            aria-label="Search clients"
          />
        </div>
      </section>

      <section className="card">
        <div className="table-wrap">
          <table className="table client-table">
            <thead>
              <tr>
                <th>Client / company</th>
                <th>Client email</th>
                <th>Contact person</th>
                <th>Contact phone</th>
                <th>Contact email</th>
                <th className="center">Edit</th>
                <th className="center">Delete</th>
              </tr>
            </thead>
            <tbody>
              {page.items.map((c) => {
                // One line per contact person, so each name lines up with its phone and email.
                const contacts = c.contacts.length ? c.contacts : [null]
                return (
                  <tr key={c.id} className="clickable" onClick={() => { setNotice(null); setEditing(c) }}>
                    <td className="client-name">{c.name}</td>
                    <td>{c.email ? <a href={`mailto:${c.email}`} onClick={(e) => e.stopPropagation()}>{c.email}</a> : dash}</td>
                    <td>{contacts.map((p, i) => <div key={i} className="client-line">{p?.name || dash}</div>)}</td>
                    <td>{contacts.map((p, i) => <div key={i} className="client-line num">{p?.phone || dash}</div>)}</td>
                    <td>
                      {contacts.map((p, i) => (
                        <div key={i} className="client-line">
                          {p?.email ? <a href={`mailto:${p.email}`} onClick={(e) => e.stopPropagation()}>{p.email}</a> : dash}
                        </div>
                      ))}
                    </td>
                    <td className="center" onClick={(e) => e.stopPropagation()}>
                      <button className="icon-action" title="Edit client" aria-label={`Edit ${c.name}`}
                        onClick={() => { setNotice(null); setEditing(c) }}>
                        <EditIcon />
                      </button>
                    </td>
                    <td className="center" onClick={(e) => e.stopPropagation()}>
                      <button className="icon-action icon-action-danger" title="Delete client" aria-label={`Delete ${c.name}`}
                        onClick={() => { setNotice(null); setDeleting(c) }}>
                        <TrashIcon />
                      </button>
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
          {!loading && page.items.length === 0 && (
            <div className="empty">
              {q ? 'No clients match this search.' : 'No clients yet. Add the first one with “+ Add client”.'}
            </div>
          )}
        </div>
        <Pagination
          total={page.total}
          limit={limit}
          offset={offset}
          onChange={(o) => updateParams({ offset: o || '' }, { resetPage: false })}
          onLimitChange={(l) => updateParams({ limit: l === DEFAULT_LIMIT ? '' : l })}
        />
      </section>

      {editing && (
        <ClientDrawer
          client={editing === 'new' ? null : editing}
          onClose={() => setEditing(null)}
          onSaved={(saved) => {
            setNotice(editing === 'new' ? `${saved.name} was added.` : `${saved.name} was saved.`)
            setEditing(null)
            load().catch(setError)
          }}
        />
      )}
      {deleting && (
        <ConfirmModal title={`Delete ${deleting.name}?`} danger confirmLabel="Delete client" busy={busy}
          onConfirm={remove} onClose={() => setDeleting(null)}>
          <p>
            This removes <strong>{deleting.name}</strong> and its {deleting.contacts.length || 'no'} contact
            person{deleting.contacts.length === 1 ? '' : 's'} from the Client Pool. This can't be undone.
          </p>
        </ConfirmModal>
      )}
    </div>
  )
}
