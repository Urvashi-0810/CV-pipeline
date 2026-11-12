import { useCallback, useEffect, useMemo, useState } from 'react'
import { talentApi } from '../../api/talentClient'
import { Alert, Badge, Button, Card, ErrorAlert, Field, fieldErrorsOf } from '../../components/ui'
import { useTalentAdmin } from './TalentAdminGate'

// Every Talent Pool setting, saved to the backend's .env. Talent Pool admin only.
// Secrets are never shown: the page only says whether each is set, and a new value replaces it.
const TESTS = { llm: 'llm', mail: 'mail', s3: 's3', database: 'database' }
const TEST_LABELS = { llm: 'Test the AI', mail: 'Test mailbox login', s3: 'Test bucket access', database: 'Test database' }
const isSecret = (s) => s.kind === 'secret' || s.kind === 'url'

function SettingInput({ setting, value, onChange, cleared, onClear, id }) {
  if (isSecret(setting)) {
    return (
      <div className="stack-sm">
        <input id={id} type={setting.kind === 'secret' ? 'password' : 'text'} autoComplete="off" value={value}
          placeholder={setting.is_set && !cleared ? 'Leave blank to keep the current value' : 'Not set'}
          onChange={(e) => onChange(e.target.value)} />
        <span className="talent-secret-state small">
          {cleared ? (
            <span>Will be removed when you save.</span>
          ) : setting.is_set ? (
            <span>
              Set{setting.hint ? ` · ${setting.hint}` : ''}
              {setting.set_via ? ` (saved as ${setting.set_via})` : ''}
            </span>
          ) : (
            <span className="muted">Not set</span>
          )}
          {setting.is_set && (
            <button type="button" className="link-btn" onClick={() => onClear(!cleared)}>
              {cleared ? 'Keep it' : 'Remove'}
            </button>
          )}
        </span>
      </div>
    )
  }
  if (setting.kind === 'choice' || setting.kind === 'bool') {
    const options = setting.kind === 'bool' ? [['true', 'Yes'], ['false', 'No']] : setting.choices.map((c) => [c, c])
    return (
      <select id={id} value={value} onChange={(e) => onChange(e.target.value)}>
        {options.map(([v, label]) => (
          <option key={v} value={v}>{label}</option>
        ))}
      </select>
    )
  }
  const type = setting.kind === 'int' || setting.kind === 'float' ? 'number' : setting.kind === 'date' ? 'date' : 'text'
  return (
    <input id={id} type={type} value={value} onChange={(e) => onChange(e.target.value)}
      min={setting.minimum ?? undefined} max={setting.maximum ?? undefined}
      step={setting.kind === 'float' ? 'any' : undefined}
      placeholder={setting.default ? `Default: ${setting.default}` : undefined} />
  )
}

function initialForm(data) {
  const form = {}
  for (const group of data.groups) {
    for (const s of group.settings) form[s.key] = isSecret(s) ? '' : (s.value ?? '')
  }
  return form
}

export default function TalentAdminSettingsPage() {
  const { handleError } = useTalentAdmin()
  const [data, setData] = useState(null)
  const [form, setForm] = useState({})
  const [clear, setClear] = useState([])
  const [error, setError] = useState(null)
  const [saved, setSaved] = useState(null)
  const [busy, setBusy] = useState(false)
  const [tests, setTests] = useState({})

  const apply = useCallback((next) => {
    setData(next)
    setForm(initialForm(next))
    setClear([])
  }, [])

  useEffect(() => {
    talentApi('/admin/settings').then(apply).catch((err) => !handleError(err) && setError(err))
  }, [apply, handleError])

  const original = useMemo(() => (data ? initialForm(data) : {}), [data])
  const changed = useMemo(
    () => Object.fromEntries(Object.entries(form).filter(([key, value]) => value !== original[key])),
    [form, original],
  )
  const changeCount = Object.keys(changed).length + clear.length
  const errors = fieldErrorsOf(error)

  async function save() {
    setBusy(true)
    setError(null)
    setSaved(null)
    try {
      const result = await talentApi('/admin/settings', { method: 'PUT', body: { values: changed, clear } })
      apply(result.settings)
      setSaved(result)
      setTests({})
    } catch (err) {
      if (!handleError(err)) setError(err)
    } finally {
      setBusy(false)
    }
  }

  async function runTest(target) {
    setTests((t) => ({ ...t, [target]: { running: true } }))
    try {
      const result = await talentApi(`/admin/settings/test/${target}`, { method: 'POST' })
      setTests((t) => ({ ...t, [target]: result }))
    } catch (err) {
      if (!handleError(err)) setTests((t) => ({ ...t, [target]: { ok: false, message: err.message } }))
    }
  }

  if (!data) return error ? <ErrorAlert error={error} /> : <p className="muted">Loading…</p>

  return (
    <div className="stack talent-settings">
      <p className="muted small">
        Saved to <span className="mono">{data.env_file}</span>. Most changes apply at once. Settings marked
        “Restart needed” apply after the Talent Pool server is restarted.
      </p>
      {data.restart_pending.length > 0 && (
        <Alert tone="info" title="Restart the Talent Pool server to apply some saved changes">
          Waiting for a restart: {data.restart_pending.join(', ')}.
        </Alert>
      )}
      {saved && (
        <Alert tone="success" title={saved.saved.length ? `Saved ${saved.saved.length} setting${saved.saved.length === 1 ? '' : 's'}.` : 'Nothing to save.'}>
          {saved.restart_required.length > 0 && `Restart the Talent Pool server to apply: ${saved.restart_required.join(', ')}.`}
        </Alert>
      )}
      {error && !error.fieldErrors.length && <ErrorAlert error={error} />}

      {data.groups.map((group) => {
        const target = TESTS[group.id]
        const test = tests[target]
        return (
          <Card key={group.id} title={group.label}
            actions={target && (
              <Button variant="ghost" size="sm" disabled={test?.running || changeCount > 0}
                title={changeCount > 0 ? 'Save your changes first' : undefined} onClick={() => runTest(target)}>
                {test?.running ? 'Testing…' : TEST_LABELS[target]}
              </Button>
            )}>
            <p className="muted small">{group.help}</p>
            {test && !test.running && (
              <Alert tone={test.ok ? 'success' : 'error'} title={test.ok ? 'Connection works' : 'Connection failed'}>
                {test.message}
              </Alert>
            )}
            <div className="talent-form-grid">
              {group.settings.map((s) => (
                <div key={s.key}>
                  <Field
                    label={
                      <>
                        {s.label}
                        {s.restart && <> <Badge tone={s.pending_restart ? 'amber' : 'neutral'}>{s.pending_restart ? 'Waiting for restart' : 'Restart needed'}</Badge></>}
                      </>
                    }
                    error={errors[s.key]}
                    hint={[s.help, s.kind === 'list' && !s.help ? 'Comma separated.' : null, `${s.key}`].filter(Boolean).join(' · ')}>
                    {(id) => (
                      <SettingInput id={id} setting={s} value={form[s.key] ?? ''}
                        onChange={(v) => setForm((f) => ({ ...f, [s.key]: v }))}
                        cleared={clear.includes(s.key)}
                        onClear={(on) => setClear((c) => (on ? [...c, s.key] : c.filter((k) => k !== s.key)))} />
                    )}
                  </Field>
                </div>
              ))}
            </div>
          </Card>
        )
      })}

      <div className={`talent-save-bar ${changeCount ? 'is-dirty' : ''}`}>
        <span className="small">{changeCount ? `${changeCount} unsaved change${changeCount === 1 ? '' : 's'}` : 'No unsaved changes'}</span>
        <div className="row-end">
          <Button variant="ghost" disabled={!changeCount || busy} onClick={() => apply(data)}>
            Discard
          </Button>
          <Button disabled={!changeCount || busy} onClick={save}>
            {busy ? 'Saving…' : 'Save settings'}
          </Button>
        </div>
      </div>
    </div>
  )
}
