import { useCallback, useEffect, useMemo, useState } from 'react'
import { talentApi } from '../api/talentClient'
import { Alert, Badge, Button, ConfirmModal, Drawer, ErrorAlert, Field, fieldErrorsOf, formatDate, formatDateTime } from '../components/ui'
import { useTalentAuth } from './TalentAuthContext'

// Every editable field, in the order shown. Lists are edited as comma-separated text.
const FIELDS = [
  ['full_name', 'Name'],
  ['job_title', 'Trade (current/last job title)'],
  ['company', 'Current/last company'],
  ['passport_number', 'Passport No.'],
  ['email', 'Email'],
  ['phone', 'Phone'],
  ['date_of_birth', 'Date of birth (YYYY-MM-DD)'],
  ['age', 'Age (as stated in the CV)'],
  ['experience_years', 'Experience (years)'],
  ['location', 'Location'],
  ['skills', 'Skills (comma separated)'],
  ['certifications', 'Certifications (comma separated)'],
  ['languages', 'Languages (comma separated)'],
  ['education', 'Education', 'textarea'],
  ['summary', 'Summary', 'textarea'],
]
const LABELS = Object.fromEntries(FIELDS.map(([key, label]) => [key, label.replace(/ \(.*\)$/, '')]))

function asText(value) {
  if (value == null) return ''
  return Array.isArray(value) ? value.join(', ') : String(value)
}

function sourceLabel(cv) {
  return cv.source_type === 'manual' ? 'Manual upload' : cv.mail_account || 'Mail'
}

function CvViewer({ cv }) {
  const [url, setUrl] = useState(null)
  const [error, setError] = useState(null)

  useEffect(() => {
    setUrl(null)
    setError(null)
    if (!cv) return
    if (!cv.viewable) {
      setError({ title: 'This CV is not in S3', message: 'Its upload failed or the file is missing. Retry failed S3 uploads on the Pipeline screen.' })
      return
    }
    let stale = false
    talentApi(`/talent/cvs/${cv.id}/url`)
      .then((data) => !stale && setUrl(data.url))
      .catch((err) => !stale && setError(err))
    return () => {
      stale = true
    }
    // Only a different CV (or its S3 state) needs a new link; saving the details must not reload the PDF.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [cv?.id, cv?.viewable])

  if (!cv) return <div className="talent-cv-empty muted">No CV on file.</div>
  if (error) return <div className="talent-cv-empty"><ErrorAlert error={error} /></div>
  if (!url) return <div className="talent-cv-empty muted">Loading CV…</div>
  return <iframe className="talent-cv-frame" src={url} title={`CV ${cv.file_name}`} />
}

async function download(cv, setError) {
  try {
    const { url } = await talentApi(`/talent/cvs/${cv.id}/url?download=true`)
    window.location.assign(url)
  } catch (err) {
    setError(err)
  }
}

function ReviewItem({ flag, onAction, busy }) {
  if (flag.type === 'possible_duplicate') {
    const other = flag.other_candidate
    return (
      <div className="talent-review-item">
        <p>
          <Badge tone="amber">Possible duplicate</Badge> <span className="muted small">{flag.reason}</span>
        </p>
        {other && (
          <dl className="details">
            <dt>Other candidate</dt>
            <dd>
              <strong>{other.full_name || '—'}</strong> · {other.job_title || 'no trade'}
            </dd>
            <dt>Passport</dt>
            <dd className="mono">{other.passport_number || '—'}</dd>
            <dt>Contact</dt>
            <dd>{[other.email, other.phone].filter(Boolean).join(' · ') || '—'}</dd>
          </dl>
        )}
        <div className="row-end">
          <Button variant="ghost" size="sm" disabled={busy} onClick={() => onAction(`/talent/flags/${flag.id}/different-people`)}>
            Different people
          </Button>
          <Button size="sm" disabled={busy} onClick={() => onAction(`/talent/flags/${flag.id}/merge`, null, 'merge')}>
            Same person → merge
          </Button>
        </div>
      </div>
    )
  }
  if (flag.type === 'edit_replaced') {
    return (
      <div className="talent-review-item">
        <p>
          <Badge tone="amber">Edit replaced</Badge> <span className="small">{flag.message}</span>
        </p>
        <table className="table table-compact">
          <thead>
            <tr>
              <th>Field</th>
              <th>Your edit</th>
              <th>New CV</th>
              <th aria-label="Actions" />
            </tr>
          </thead>
          <tbody>
            {flag.fields.map((item) => (
              <tr key={item.field}>
                <td>{LABELS[item.field] || item.field}</td>
                <td>{asText(item.manual_value) || <span className="muted">—</span>}</td>
                <td>{asText(item.new_value)}</td>
                <td className="row-actions">
                  {item.kept ? (
                    <span className="muted small">Kept</span>
                  ) : (
                    <Button variant="ghost" size="sm" disabled={busy}
                      onClick={() => onAction(`/talent/flags/${flag.id}/keep-edit`, { field: item.field })}>
                      Keep my edit
                    </Button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        <div className="row-end">
          <Button variant="ghost" size="sm" disabled={busy} onClick={() => onAction(`/talent/flags/${flag.id}/accept`)}>
            Accept the new values
          </Button>
        </div>
      </div>
    )
  }
  return (
    <div className="talent-review-item">
      <p>
        <Badge tone="amber">Check passport match</Badge> <span className="small">{flag.message}</span>
      </p>
      <div className="row-end">
        <Button variant="ghost" size="sm" disabled={busy} onClick={() => onAction(`/talent/flags/${flag.id}/accept`)}>
          It's the same person
        </Button>
      </div>
    </div>
  )
}

export default function TalentCandidateDrawer({ candidateId, onClose, onChanged, onSwitch }) {
  const { me } = useTalentAuth()
  const [detail, setDetail] = useState(null)
  const [history, setHistory] = useState([])
  const [form, setForm] = useState({})
  const [viewing, setViewing] = useState(null)
  const [error, setError] = useState(null)
  const [saveError, setSaveError] = useState(null)
  const [saved, setSaved] = useState(false)
  const [busy, setBusy] = useState(false)
  const [confirm, setConfirm] = useState(null)

  const load = useCallback(async () => {
    const [data, past] = await Promise.all([
      talentApi(`/talent/candidates/${candidateId}`),
      talentApi(`/talent/candidates/${candidateId}/history`),
    ])
    setDetail(data)
    setHistory(past.items)
    setForm(Object.fromEntries(FIELDS.map(([key]) => [key, asText(data.values[key])])))
    setViewing((current) => data.cvs.find((cv) => cv.id === current?.id) || data.cvs[0] || null)
  }, [candidateId])

  useEffect(() => {
    setDetail(null)
    setError(null)
    load().catch(setError)
  }, [load])

  const changed = useMemo(() => {
    if (!detail) return {}
    return Object.fromEntries(FIELDS.map(([key]) => key).filter((key) => form[key] !== asText(detail.values[key]))
      .map((key) => [key, form[key]]))
  }, [form, detail])
  const dirty = Object.keys(changed).length > 0

  async function save(event) {
    event.preventDefault()
    setBusy(true)
    setSaveError(null)
    setSaved(false)
    try {
      await talentApi(`/talent/candidates/${candidateId}`, {
        method: 'PATCH',
        body: { values: changed },
        headers: { 'If-Match': `"${detail.row_version}"` },
      })
      await load()
      setSaved(true)
      onChanged()
    } catch (err) {
      setSaveError(err)
    } finally {
      setBusy(false)
    }
  }

  async function act(path, body, kind) {
    setBusy(true)
    setError(null)
    try {
      const result = await talentApi(path, { method: 'POST', body })
      onChanged()
      // A merge keeps the older record. If that's the other one, show it instead.
      if (kind === 'merge' && result.candidate_id !== candidateId) onSwitch(result.candidate_id)
      else await load()
    } catch (err) {
      setError(err)
    } finally {
      setBusy(false)
    }
  }

  async function remove() {
    setBusy(true)
    try {
      await talentApi(`/talent/candidates/${candidateId}`, { method: 'DELETE' })
      onChanged()
      onClose()
    } catch (err) {
      setError(err)
      setConfirm(null)
    } finally {
      setBusy(false)
    }
  }

  const values = detail?.values || {}
  const errors = fieldErrorsOf(saveError)

  return (
    <Drawer
      className="talent-drawer"
      title={values.full_name || (detail ? 'Unnamed candidate' : 'Loading…')}
      subtitle={detail && [values.job_title, values.passport_number && `Passport ${values.passport_number}`]
        .filter(Boolean).join(' · ')}
      onClose={onClose}
      footer={detail && (
        <>
          <span className="muted small">
            {detail.cvs.length} CV{detail.cvs.length === 1 ? '' : 's'} · last received {formatDate(detail.received_at)}
          </span>
          {me?.admin_active && (
            <Button variant="danger" size="sm" disabled={busy} onClick={() => setConfirm('delete')}>
              Delete candidate
            </Button>
          )}
        </>
      )}
    >
      <ErrorAlert error={error} />
      {detail && (
        <div className="talent-detail">
          <section className="talent-detail-cv">
            <div className="talent-cv-bar">
              <select value={viewing?.id || ''} aria-label="CV version"
                onChange={(e) => setViewing(detail.cvs.find((cv) => cv.id === Number(e.target.value)))}>
                {detail.cvs.map((cv) => (
                  <option key={cv.id} value={cv.id}>
                    {formatDate(cv.received_at)} · {sourceLabel(cv)}{cv.is_latest ? ' (latest)' : ''}
                  </option>
                ))}
              </select>
              {viewing && (
                <Button variant="ghost" size="sm" disabled={!viewing.viewable} onClick={() => download(viewing, setError)}>
                  Download
                </Button>
              )}
            </div>
            <CvViewer cv={viewing} />
          </section>

          <section className="talent-detail-side stack">
            {detail.flags.length > 0 && (
              <div className="stack-sm">
                <h3 className="talent-section-title">Needs review</h3>
                {detail.flags.map((flag) => (
                  <ReviewItem key={flag.id} flag={flag} onAction={act} busy={busy} />
                ))}
              </div>
            )}

            <form onSubmit={save} className="stack-sm">
              <h3 className="talent-section-title">Details</h3>
              {saved && !dirty && <Alert tone="success">Saved. The Excel is updated in a few seconds.</Alert>}
              {saveError && !saveError.fieldErrors.length && (
                <Alert title={saveError.title}>
                  {saveError.message !== saveError.title ? saveError.message : null}
                  {saveError.is?.('candidate-changed') && (
                    <> <button type="button" className="link-btn" onClick={() => load().then(() => setSaveError(null))}>Reload</button></>
                  )}
                </Alert>
              )}
              <div className="talent-form-grid">
                {FIELDS.map(([key, label, kind]) => (
                  <div key={key} className={kind === 'textarea' ? 'talent-span-2' : ''}>
                    <Field label={`${label}${detail.manual_fields.includes(key) ? ' · edited' : ''}`} error={errors[key]}
                      hint={key === 'age' && detail.age_from_birth_date ? `Shown as ${detail.age}, worked out from the date of birth.` : null}>
                      {(id) => kind === 'textarea' ? (
                        <textarea id={id} rows={3} value={form[key]} onChange={(e) => setForm({ ...form, [key]: e.target.value })} />
                      ) : (
                        <input id={id} value={form[key]} onChange={(e) => setForm({ ...form, [key]: e.target.value })} />
                      )}
                    </Field>
                  </div>
                ))}
              </div>
              <div className="row-end">
                {dirty && (
                  <Button type="button" variant="ghost" disabled={busy}
                    onClick={() => setForm(Object.fromEntries(FIELDS.map(([key]) => [key, asText(values[key])])))}>
                    Discard changes
                  </Button>
                )}
                <Button type="submit" disabled={busy || !dirty}>
                  {busy ? 'Saving…' : 'Save changes'}
                </Button>
              </div>
            </form>

            <div className="stack-sm">
              <h3 className="talent-section-title">CV versions</h3>
              <table className="table table-compact">
                <tbody>
                  {detail.cvs.map((cv) => (
                    <tr key={cv.id}>
                      <td className="num">{formatDateTime(cv.received_at)}</td>
                      <td>
                        {sourceLabel(cv)}
                        {cv.mail_subject && <div className="muted small">{cv.mail_subject}</div>}
                      </td>
                      <td>{cv.viewable ? null : <Badge tone="red">{cv.s3_status === 'missing' ? 'Not in S3' : 'S3 failed'}</Badge>}</td>
                      <td className="row-actions">
                        <Button variant="ghost" size="sm" disabled={!cv.viewable} onClick={() => setViewing(cv)}>
                          View
                        </Button>
                        <Button variant="ghost" size="sm" disabled={!cv.viewable} onClick={() => download(cv, setError)}>
                          Download
                        </Button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>

            <div className="stack-sm">
              <h3 className="talent-section-title">Edit history</h3>
              {history.length === 0 ? (
                <p className="muted small">No manual edits yet.</p>
              ) : (
                <ul className="talent-history">
                  {history.map((h) => (
                    <li key={h.id}>
                      <span className="muted small">{formatDateTime(h.created_at)}</span>{' '}
                      <strong>{h.changed_by || 'New CV'}</strong>{' '}
                      {h.kind === 'replaced_by_cv' ? 'replaced' : h.kind === 'keep_my_edit' ? 'kept the edit of' : 'changed'}{' '}
                      <strong>{LABELS[h.field] || h.field}</strong>: {h.old_value || '—'} → {h.new_value || '—'}
                    </li>
                  ))}
                </ul>
              )}
            </div>
          </section>
        </div>
      )}
      {confirm === 'delete' && (
        <ConfirmModal title="Delete this candidate?" danger confirmLabel="Delete everywhere" busy={busy}
          onConfirm={remove} onClose={() => setConfirm(null)}>
          <p>
            <strong>{values.full_name || 'This candidate'}</strong> and all {detail.cvs.length} of their CVs will be removed from
            the Talent Pool, S3, this computer and the Excel. This can't be undone.
          </p>
          <p className="muted small">If they send a CV again later, they come back as a new candidate.</p>
        </ConfirmModal>
      )}
    </Drawer>
  )
}
