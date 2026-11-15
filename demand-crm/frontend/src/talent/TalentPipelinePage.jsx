import { useCallback, useEffect, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { talentApi } from '../api/talentClient'
import { Alert, Badge, Button, Card, ConfirmModal, ErrorAlert, Field, formatDateTime } from '../components/ui'

// Run fetch and extract, control them (pause, resume, cancel, retry) and see their history.
// Open to any Talent Pool user. Pause and cancel act at safe points: after the current CV,
// or between batches of 50 mails, so nothing is left half done.
// Built only from the CRM's own components (Card, Button, Badge, Alert, ConfirmModal, segmented, kpi, table).

const PATHS = { fetch: '/fetch', extract: '/extract', run: '/run', 's3-retry': '/talent/s3/retry' }
const NAMES = {
  fetch: 'Fetch mail', extract: 'Extract CVs', run: 'Fetch CVs', upload: 'Manual upload',
  delete: 'Deleting a candidate', 's3-retry': 'Retry S3 uploads', reextract: 'Re-extract with AI',
}
const ACTIVE = ['queued', 'running', 'paused']
const CONTROLLABLE = ['fetch', 'extract', 'run', 's3-retry', 'reextract']
const POLL_MS = 1200
const fmt = (n) => (n ?? 0).toLocaleString('en-IN')

function duration(from, to) {
  if (!from) return '—'
  const seconds = Math.max(0, Math.round(((to ? new Date(to) : new Date()) - new Date(from)) / 1000))
  const h = Math.floor(seconds / 3600)
  const m = Math.floor((seconds % 3600) / 60)
  const s = String(seconds % 60).padStart(2, '0')
  return h ? `${h}:${String(m).padStart(2, '0')}:${s}` : `${m}:${s}`
}

// The few numbers that say what a run did, for its kind.
function outcome(kind, s) {
  if (!s) return []
  if (s.error) return []
  if (kind === 'run') return [...outcome('fetch', s.fetch), ...outcome('extract-after', s.extract)]
  if (kind === 'fetch') {
    return [
      ['Mails scanned', s.mails_scanned], ['CVs saved', s.attachments_saved], ['Duplicates skipped', s.duplicates_skipped],
      ['CVs extracted', s.cvs_extracted], ['CVs failed', s.cvs_failed, 'bad'],
      ['Password-protected, skipped', s.cvs_skipped_protected || undefined],
    ]
  }
  if (kind === 'extract' || kind === 'extract-after') {
    return [
      [kind === 'extract' ? 'CVs extracted' : 'More CVs extracted', s.extracted], ['CVs failed', s.failed, 'bad'],
      ['Password-protected, skipped', s.skipped_protected || undefined],
      ['Already done', s.skipped_already_done],
    ]
  }
  if (kind === 's3-retry') return [['Uploaded', s.uploaded], ['Failed again', s.failed, 'bad'], ['No local file', s.no_local_file, 'bad']]
  if (kind === 'reextract') return [['Candidates updated', s.updated], ['Failed', s.failed, 'bad'], ['No CV file', s.no_cv_file, 'bad']]
  return []
}

function stateOf(task) {
  if (!task) return { tone: 'neutral', label: 'Idle' }
  if (task.cancel_requested && ACTIVE.includes(task.status)) return { tone: 'amber', label: 'Stopping' }
  if (task.status === 'paused') return { tone: 'amber', label: 'Paused' }
  if (task.pause_requested) return { tone: 'amber', label: 'Pausing' }
  if (task.status === 'queued') return { tone: 'blue', label: 'Waiting' }
  return { tone: 'blue', label: 'Running' }
}

function Numbers({ items }) {
  const shown = items.filter(([, value]) => value !== undefined && value !== null)
  if (!shown.length) return null
  return (
    <div className="pl-numbers">
      {shown.map(([label, value, bad]) => (
        <div key={label} className="stat">
          <div className="label">{label}</div>
          <div className={`value ${bad && value ? 'pl-bad' : ''}`}>{fmt(value)}</div>
        </div>
      ))}
    </div>
  )
}

const dmy = (iso) => (iso ? iso.split('-').reverse().join('-') : '')

// What a fetch reads: one mailbox, one of its folders, and either new mail or a date range.
function FetchOptions({ mailboxes, value, onChange }) {
  const [folders, setFolders] = useState([])
  const [folderError, setFolderError] = useState(null)
  const [loading, setLoading] = useState(false)
  const mailbox = mailboxes.find((m) => m.id === value.mailboxId)

  useEffect(() => {
    setFolders([])
    setFolderError(null)
    if (!mailbox) return
    let stale = false
    setLoading(true)
    talentApi(`/mailboxes/${mailbox.id}/folders`)
      .then((data) => !stale && setFolders(data.items))
      .catch((err) => !stale && setFolderError(err))
      .finally(() => !stale && setLoading(false))
    return () => {
      stale = true
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [mailbox?.id])

  if (!mailboxes.length) {
    return (
      <Alert tone="info" title="No mailboxes yet">
        A Talent Pool admin adds them on <Link to="/talent/admin/mailboxes">Admin → Mailboxes</Link>.
      </Alert>
    )
  }
  const folderChoices = folders.length ? folders : [value.folder || mailbox?.default_folder].filter(Boolean)
  const lastFetch = mailbox?.last_fetch?.[value.folder]
  return (
    <div className="stack-sm">
      <div className="talent-form-grid">
        <Field label="Mailbox">
          {(id) => (
            <select id={id} value={value.mailboxId || ''} onChange={(e) => {
              const next = mailboxes.find((m) => m.id === Number(e.target.value))
              onChange({ ...value, mailboxId: next?.id, folder: next?.default_folder || 'INBOX' })
            }}>
              {mailboxes.map((m) => <option key={m.id} value={m.id}>{m.name} ({m.email})</option>)}
            </select>
          )}
        </Field>
        <Field label={loading ? 'Folder · loading…' : 'Folder'}
          error={folderError ? `Couldn't list folders: ${folderError.message}` : null}>
          {(id) => (
            <select id={id} value={value.folder} onChange={(e) => onChange({ ...value, folder: e.target.value })}>
              {folderChoices.map((f) => <option key={f} value={f}>{f}</option>)}
            </select>
          )}
        </Field>
      </div>
      <div className="field">
        <label>Which mail</label>
        <div className="pl-row">
          <div className="segmented" role="radiogroup" aria-label="Which mail">
            <button type="button" role="radio" aria-checked={value.period === 'new'}
              className={value.period === 'new' ? 'active' : ''} onClick={() => onChange({ ...value, period: 'new' })}>
              New mail
            </button>
            <button type="button" role="radio" aria-checked={value.period === 'range'}
              className={value.period === 'range' ? 'active' : ''} onClick={() => onChange({ ...value, period: 'range' })}>
              Date range
            </button>
          </div>
          {value.period === 'range' ? (
            <div className="date-range">
              <span>From</span>
              <input type="date" aria-label="From date" value={value.since} onChange={(e) => onChange({ ...value, since: e.target.value })} />
              <span>to</span>
              <input type="date" aria-label="To date" value={value.until} min={value.since || undefined}
                onChange={(e) => onChange({ ...value, until: e.target.value })} />
            </div>
          ) : null}
        </div>
        <p className="field-hint">
          {value.period === 'range'
            ? (value.since && value.until ? `${dmy(value.since)} to ${dmy(value.until)}, both days included.` : 'Choose both dates.')
            : lastFetch ? `Mail since the last fetch (${formatDateTime(lastFetch)}).` : 'Never fetched from this folder: reads all of its mail.'}
        </p>
      </div>
    </div>
  )
}

// What the run reads, as one line: "Recruitment · INBOX · 01-01-2024 to 31-03-2024"
function describeSource(about) {
  const s = about?.fetch || about
  if (!s?.mailbox) return null
  const period = s.mode === 'range' ? `${dmy(s.since)} to ${dmy(s.until)}` : 'new mail'
  return `${s.mailbox} · ${s.folder} · ${period}`
}

// A run's recorded mailbox, folder and dates, if it has them; otherwise a retry uses the choices on screen.
const sourceOf = (about) => ((about?.fetch || about)?.mailbox_id ? about : undefined)

function LiveRun({ task }) {
  const [, tick] = useState(0)
  useEffect(() => {
    const timer = setInterval(() => tick((n) => n + 1), 1000)
    return () => clearInterval(timer)
  }, [])

  const percent = task.total ? Math.min(100, Math.round((100 * task.done) / task.total)) : null
  const unit = task.kind === 'fetch' || task.kind === 'run' ? 'mails' : task.kind === 'reextract' ? 'candidates' : 'files'
  const state = stateOf(task)

  return (
    <div className="stack-sm">
      <div className="pl-row pl-spread">
        <div>
          <strong>{NAMES[task.kind] || task.kind}</strong>
          {describeSource(task.params) && <div className="muted small">{describeSource(task.params)}</div>}
        </div>
        <span className="muted small num">Elapsed {duration(task.started_at || task.created_at)}</span>
      </div>
      <div className={`pl-bar ${state.tone === 'amber' ? 'pl-bar-amber' : ''}`} role="progressbar" aria-valuemin={0} aria-valuemax={100}
        aria-valuenow={percent ?? undefined} aria-label="Progress">
        <div className={`pl-bar-fill ${percent === null ? 'is-unknown' : ''}`} style={{ width: `${percent ?? 100}%` }} />
      </div>
      <div className="pl-row pl-spread small">
        <span className="num">
          {task.total ? `${fmt(task.done)} of ${fmt(task.total)} ${unit}` : task.status === 'queued' ? 'Waiting to start' : 'Starting…'}
        </span>
        {percent !== null && <strong className="num">{percent}%</strong>}
      </div>
      <p className="muted small pl-current">
        {task.cancel_requested ? 'Stopping after the current CV. Everything finished so far is kept.'
          : task.status === 'paused' ? 'Paused. Nothing is being processed. Resume to carry on from here.'
            : task.pause_requested ? 'Pausing after the current CV…'
              : task.current || 'Starting…'}
      </p>
      {task.controllable === undefined && CONTROLLABLE.includes(task.kind) && (
        <Alert title="Pause and Cancel need a server restart">
          The Talent Pool server is running an older version. Restart it (python main.py serve) to use Pause and Cancel.
        </Alert>
      )}
    </div>
  )
}

function Finished({ task }) {
  const name = NAMES[task.kind] || task.kind
  const heading = { done: `${name} finished`, cancelled: `${name} stopped`, error: `${name} failed` }[task.status]
  const tone = { done: 'success', cancelled: 'info', error: 'error' }[task.status]
  const result = task.result || {}
  const stoppedEarly = result.stopped_early || result.fetch?.stopped_early
  return (
    <div className="stack-sm">
      <Alert tone={tone} title={heading}>
        {describeSource(task.params) && <>{describeSource(task.params)}. </>}
        {task.status === 'cancelled' && 'Stopped with Cancel. Everything done before that is kept. '}
        {task.status === 'error' && `${task.error} `}
        Took {duration(task.started_at || task.created_at, task.finished_at)}.
      </Alert>
      {stoppedEarly && <Alert title="The mail fetch stopped early">{stoppedEarly}. Retry to carry on from there.</Alert>}
      <Numbers items={outcome(task.kind, result)} />
    </div>
  )
}

function Connection({ label, ok, detail }) {
  return (
    <li className="pl-conn">
      <span className={`pl-conn-dot ${ok ? 'is-ok' : 'is-bad'}`} aria-hidden="true" />
      <div>
        <strong>{label}</strong>
        <span className="muted small">{detail}</span>
      </div>
      <span className="sr-only">{ok ? 'set up' : 'not set up'}</span>
    </li>
  )
}

function Kpi({ label, value, tone, children }) {
  return (
    <div className={`kpi ${tone ? `pl-kpi-${tone}` : ''}`}>
      <span className="kpi-label">{label}</span>
      <span className="kpi-value">{value}</span>
      {children}
    </div>
  )
}

export default function TalentPipelinePage() {
  const [config, setConfig] = useState(null)
  const [status, setStatus] = useState(null)
  const [runs, setRuns] = useState([])
  const [task, setTask] = useState(null)
  const [finished, setFinished] = useState(null)
  const [error, setError] = useState(null)
  const [busy, setBusy] = useState(false)
  const [confirming, setConfirming] = useState(false)
  const [queueing, setQueueing] = useState(false)
  const [mailboxes, setMailboxes] = useState([])
  const [fetchOptions, setFetchOptions] = useState({ mailboxId: null, folder: 'INBOX', period: 'new', since: '', until: '' })
  const [waiting, setWaiting] = useState([])
  const timer = useRef(null)

  const loadOverview = useCallback(async () => {
    const [cfg, stat, recent, boxes] = await Promise.all([
      talentApi('/config'), talentApi('/status'), talentApi('/pipeline/runs?limit=8'), talentApi('/mailboxes'),
    ])
    setConfig(cfg)
    setStatus(stat)
    setRuns(recent.items)
    setMailboxes(boxes.items)
    // Start with the first mailbox and its usual folder chosen.
    setFetchOptions((o) => (o.mailboxId || !boxes.items.length ? o
      : { ...o, mailboxId: boxes.items[0].id, folder: boxes.items[0].default_folder }))
  }, [])

  const loadWaiting = useCallback(async () => {
    const all = await talentApi('/tasks')
    setWaiting(all.filter((t) => t.lane === 'pipeline' && t.status === 'queued' && t.controllable))
  }, [])

  const watch = useCallback((id) => {
    clearTimeout(timer.current)
    timer.current = setTimeout(async () => {
      try {
        const next = await talentApi(`/tasks/${id}`)
        loadWaiting().catch(() => {})
        if (next.status === 'queued' && next.current) {
          // Still waiting behind another run: follow that one instead.
          const all = await talentApi('/tasks')
          const running = all.find((t) => t.lane === 'pipeline' && ['running', 'paused'].includes(t.status))
          setTask(running || next)
          watch(running ? running.id : id)
        } else if (ACTIVE.includes(next.status)) {
          setTask(next)
          watch(id)
        } else {
          setTask(null)
          setConfirming(false)
          setFinished(next)
          loadOverview().catch(setError)
          findActiveRef.current?.()                 // a queued fetch may start next
        }
      } catch (err) {
        setError(err)
      }
    }, POLL_MS)
  }, [loadOverview, loadWaiting])

  // Show a run started elsewhere (another PC, or before this page was opened). Running beats waiting.
  const findActive = useCallback(async () => {
    const all = await talentApi('/tasks')
    const pipeline = all.filter((t) => t.lane === 'pipeline')
    const active = pipeline.find((t) => ['running', 'paused'].includes(t.status)) || pipeline.find((t) => t.status === 'queued')
    setWaiting(pipeline.filter((t) => t.status === 'queued' && t.controllable && t.id !== active?.id))
    if (active) {
      setTask(active)
      watch(active.id)
    }
    return active
  }, [watch])
  const findActiveRef = useRef(null)
  findActiveRef.current = findActive

  useEffect(() => {
    loadOverview().catch(setError)
    findActive().catch(() => {})
    return () => clearTimeout(timer.current)
  }, [loadOverview, findActive])

  // The body for a fetch: the chosen mailbox, folder and period (or those of a run being retried).
  function fetchBody(source) {
    if (source) {
      const s = source.fetch || source
      return { mailbox_id: s.mailbox_id, folder: s.folder, since: s.since || undefined, until: s.until || undefined }
    }
    const o = fetchOptions
    if (!o.mailboxId) throw new Error('Choose a mailbox first.')
    if (o.period === 'range' && !(o.since && o.until)) throw new Error('Choose both a from date and a to date.')
    return { mailbox_id: o.mailboxId, folder: o.folder,
      ...(o.period === 'range' ? { since: o.since, until: o.until } : {}) }
  }

  async function start(path, source) {
    setBusy(true)
    setError(null)
    try {
      const body = path === '/fetch' || path === '/run' ? fetchBody(source) : {}
      const started = await talentApi(path, { method: 'POST', body })
      if (task) {
        setQueueing(false)
        loadWaiting().catch(() => {})             // queued behind the current run
      } else {
        setFinished(null)
        setTask(started)
        watch(started.id)
      }
    } catch (err) {
      if (!err.status) {
        setError({ title: err.message })
        return
      }
      if (err.status === 409 && (await findActive())) return    // something else is running: show it
      setError(err)
    } finally {
      setBusy(false)
    }
  }

  async function control(action) {
    setBusy(true)
    setError(null)
    try {
      setTask(await talentApi(`/tasks/${task.id}/${action}`, { method: 'POST' }))
    } catch (err) {
      setError(err)
    } finally {
      setBusy(false)
    }
  }

  async function cancelWaiting(id) {
    try {
      await talentApi(`/tasks/${id}/cancel`, { method: 'POST' })
      await loadWaiting()
    } catch (err) {
      setError(err)
    }
  }

  const talent = status?.talent
  const s3Problems = (talent?.s3_failed || 0) + (talent?.s3_missing || 0)
  const failedFiles = status?.failed || []
  // CV files on disk not extracted yet: failed ones, ones left by a Cancel, or ones copied in by hand.
  const waitingFiles = Math.max(0, (status?.cv_files || 0) - (status?.extracted_ok || 0))
  const running = Boolean(task)
  // Retry repeats the last run that failed or was stopped (or just ended), from this page or the history.
  const lastRun = finished || runs.find((r) => r.status !== 'running') || null
  const retryLast = () => start(PATHS[lastRun.kind], sourceOf(lastRun.params) || sourceOf(lastRun.summary))
  const state = stateOf(task)
  const controllable = running && task.controllable === true && !task.cancel_requested
  const paused = running && (task.status === 'paused' || task.pause_requested)

  return (
    <div className="stack pl-page">
      <div className="page-header">
        <div>
          <h1 className="page-title">Pipeline</h1>
          <p className="muted small">Bring new CVs in from the mailbox and turn them into candidates.</p>
        </div>
        <Badge tone={state.tone}>{task ? `${state.label} · ${NAMES[task.kind] || task.kind}` : state.label}</Badge>
      </div>

      <ErrorAlert error={error} />

      <div className="pl-top">
        <Card title={running ? 'Current run' : finished ? 'Last run' : 'Fetch CVs'}>
          {running ? (
            <>
              <LiveRun task={task} />
              <div className="row-end">
                {paused ? (
                  <Button variant="ghost" disabled={!controllable || busy} onClick={() => control('resume')}>Resume</Button>
                ) : (
                  <Button variant="ghost" disabled={!controllable || busy || task.status === 'queued'} onClick={() => control('pause')}>
                    Pause
                  </Button>
                )}
                <Button variant="danger" disabled={!controllable || busy} onClick={() => setConfirming(true)}>Cancel run</Button>
              </div>

              {waiting.length > 0 && (
                <div className="stack-sm">
                  <h3 className="talent-section-title">Waiting to start ({waiting.length})</h3>
                  <table className="table table-compact">
                    <tbody>
                      {waiting.map((w) => (
                        <tr key={w.id}>
                          <td>
                            {NAMES[w.kind] || w.kind}
                            {describeSource(w.params) && <div className="muted small">{describeSource(w.params)}</div>}
                          </td>
                          <td className="row-actions">
                            <Button variant="ghost" size="sm" onClick={() => cancelWaiting(w.id)}>Cancel</Button>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}

              {queueing ? (
                <div className="stack-sm">
                  <h3 className="talent-section-title">Queue another fetch</h3>
                  <FetchOptions mailboxes={mailboxes} value={fetchOptions} onChange={setFetchOptions} />
                  <div className="row-end">
                    <Button variant="ghost" onClick={() => setQueueing(false)}>Cancel</Button>
                    <Button disabled={busy || !mailboxes.length} onClick={() => start('/run')}>Add to queue</Button>
                  </div>
                </div>
              ) : (
                <div>
                  <Button variant="ghost" size="sm" onClick={() => setQueueing(true)}>+ Queue another fetch</Button>
                </div>
              )}
            </>
          ) : finished ? (
            <>
              <Finished task={finished} />
              <div className="row-end">
                <Button variant="ghost" onClick={() => setFinished(null)}>Done</Button>
                {PATHS[finished.kind] && (
                  <Button disabled={busy} onClick={() => start(PATHS[finished.kind], sourceOf(finished.params))}>
                    {finished.status === 'done' ? 'Run again' : 'Retry'}
                  </Button>
                )}
              </div>
            </>
          ) : (
            <>
              <p className="muted small">
                Downloads the CVs from the chosen mailbox and folder and adds them to the Talent Pool. Files left over
                from earlier runs are picked up too.
              </p>
              <FetchOptions mailboxes={mailboxes} value={fetchOptions} onChange={setFetchOptions} />
              <div className="row-end">
                {lastRun && PATHS[lastRun.kind] && (
                  <Button variant="ghost" disabled={busy} onClick={retryLast}
                    title={describeSource(lastRun.params || lastRun.summary) || undefined}>
                    Retry last run
                  </Button>
                )}
                <Button disabled={busy || !mailboxes.length} onClick={() => start('/run')}>
                  {busy ? 'Starting…' : 'Fetch CVs'}
                </Button>
              </div>
            </>
          )}
        </Card>

        <Card title="Connections" actions={<Link className="btn btn-ghost" to="/talent/admin/settings">Settings</Link>}>
          {config ? (
            <ul className="pl-conns">
              <Connection label="Mailboxes" ok={config.mailboxes > 0}
                detail={config.mailboxes ? `${config.mailboxes} connected` : 'None yet: add them in Admin → Mailboxes'} />
              <Connection label="AI model" ok={config.providers_configured?.[config.llm_provider]}
                detail={`${config.llm_provider} · ${config.llm_provider === 'gemini' ? config.gemini_model : config.claude_model}${config.providers_configured?.[config.llm_provider] ? '' : ' · no API key'}`} />
              <Connection label="S3 storage" ok={config.s3?.configured}
                detail={config.s3?.configured ? `${config.s3.bucket}${config.s3.region ? ` · ${config.s3.region}` : ''}` : 'No bucket set'} />
              <Connection label="Database" ok={Boolean(talent)}
                detail={talent ? `${fmt(talent.candidates)} candidates` : 'Not connected'} />
            </ul>
          ) : (
            <p className="muted">Loading…</p>
          )}
        </Card>
      </div>

      <section className="pl-tiles" aria-label="Status">
        <Kpi label="CV files on disk" value={fmt(status?.cv_files)} />
        <Kpi label="Extracted" value={fmt(status?.extracted_ok)} />
        <Kpi label="Candidates" value={talent ? fmt(talent.candidates) : '—'} />
        <Kpi label="Need review" value={talent ? fmt(talent.needs_review) : '—'} tone={talent?.needs_review ? 'warn' : null}>
          {talent?.needs_review > 0 && <Link className="small" to="/talent?needs_review=true">Review them</Link>}
        </Kpi>
        <Kpi label="Failed files" value={fmt(failedFiles.length)} tone={failedFiles.length ? 'bad' : null} />
        <Kpi label="Not in S3" value={talent ? fmt(s3Problems) : '—'} tone={s3Problems ? 'bad' : null}>
          <div>
            <Button variant="ghost" size="sm" disabled={running || busy} onClick={() => start(PATHS['s3-retry'])}
              title="Compares the bucket with the Talent Pool and uploads any CV that's missing, from this computer">
              {s3Problems > 0 ? 'Retry uploads' : 'Check S3'}
            </Button>
          </div>
        </Kpi>
      </section>

      <div className="pl-bottom">
        <Card title="Run history">
          {runs.length === 0 ? (
            <p className="muted">No runs yet. Runs started here appear in this list.</p>
          ) : (
            <div className="table-wrap">
              <table className="table table-compact">
                <thead>
                  <tr>
                    <th>Run</th>
                    <th>Result</th>
                    <th>Status</th>
                    <th aria-label="Actions" />
                  </tr>
                </thead>
                <tbody>
                  {runs.map((r) => {
                    const tone = { done: 'green', cancelled: 'amber', error: 'red', running: 'blue' }[r.status]
                    const label = { done: 'Finished', cancelled: 'Stopped', error: 'Failed', running: 'Running' }[r.status]
                    const numbers = outcome(r.kind, r.summary).filter(([, v]) => v).slice(0, 3)
                    return (
                      <tr key={r.id}>
                        <td>
                          <strong>{NAMES[r.kind] || r.kind}</strong>
                          {describeSource(r.summary) && <div className="small">{describeSource(r.summary)}</div>}
                          <div className="muted small num">{formatDateTime(r.started_at)} · {duration(r.started_at, r.finished_at)}</div>
                        </td>
                        <td className="small pl-wrap">
                          {r.status === 'error' ? r.summary?.error
                            : numbers.length ? numbers.map(([name, v]) => `${fmt(v)} ${name.toLowerCase()}`).join(' · ')
                              : 'Nothing new'}
                        </td>
                        <td><Badge tone={tone}>{label}</Badge></td>
                        <td className="row-actions">
                          {(r.status === 'error' || r.status === 'cancelled') && PATHS[r.kind] && (
                            <Button variant="ghost" size="sm" disabled={running || busy} onClick={() => start(PATHS[r.kind], sourceOf(r.summary))}>
                              Retry
                            </Button>
                          )}
                        </td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            </div>
          )}
        </Card>

        <Card title={`Failed files (${failedFiles.length})`}
          actions={(failedFiles.length > 0 || waitingFiles > 0) && (
            <Button variant="ghost" disabled={running || busy} onClick={() => start('/extract')}
              title="Extract the CV files on disk that aren't done yet: failed ones and any copied in by hand">
              {failedFiles.length ? 'Retry failed files' : `Extract ${fmt(waitingFiles)} waiting file${waitingFiles === 1 ? '' : 's'}`}
            </Button>
          )}>
          {failedFiles.length === 0 ? (
            <p className="muted">No failed files.</p>
          ) : (
            <div className="table-wrap pl-scroll">
              <table className="table table-compact">
                <tbody>
                  {failedFiles.map((f) => (
                    <tr key={`${f.file}-${f.at}`}>
                      <td className="pl-wrap">
                        <span className="mono">{f.file}</span>
                        <div className="small pl-error">{f.error}</div>
                      </td>
                      <td className="muted small num">{formatDateTime(f.at)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Card>
      </div>

      {confirming && running && (
        <ConfirmModal title="Cancel this run?" danger confirmLabel="Cancel run" busy={busy}
          onConfirm={() => {
            setConfirming(false)
            control('cancel')
          }}
          onClose={() => setConfirming(false)}>
          <p>The run stops after the current CV. Everything done so far is kept, and the next run carries on from there.</p>
        </ConfirmModal>
      )}
    </div>
  )
}
