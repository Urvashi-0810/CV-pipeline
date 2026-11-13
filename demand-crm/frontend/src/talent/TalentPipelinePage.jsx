import { useCallback, useEffect, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { talentApi } from '../api/talentClient'
import { Alert, Badge, Button, ErrorAlert, formatDateTime } from '../components/ui'

// Run fetch and extract, control them (pause, resume, cancel, retry) and see their history.
// Open to any Talent Pool user. Pause and cancel act at safe points: after the current CV,
// or between batches of 50 mails, so nothing is left half done.

const MODES = [
  { kind: 'fetch', path: '/fetch', title: 'Fetch new mail', text: 'Download new CVs from the mailbox and extract them as they arrive.' },
  { kind: 'extract', path: '/extract', title: 'Extract waiting CVs', text: 'Process CV files not done yet, including ones that failed before.' },
  { kind: 'run', path: '/run', title: 'Fetch, then extract', text: 'Fetch new mail, then extract anything still waiting.' },
]
const PATHS = { fetch: '/fetch', extract: '/extract', run: '/run', 's3-retry': '/talent/s3/retry' }
const NAMES = {
  fetch: 'Fetch mail', extract: 'Extract CVs', run: 'Fetch, then extract', upload: 'Manual upload',
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
    ]
  }
  if (kind === 'extract' || kind === 'extract-after') {
    return [
      [kind === 'extract' ? 'CVs extracted' : 'More CVs extracted', s.extracted], ['CVs failed', s.failed, 'bad'],
      ['Already done', s.skipped_already_done],
    ]
  }
  if (kind === 's3-retry') return [['Uploaded', s.uploaded], ['Failed again', s.failed, 'bad'], ['No local file', s.no_local_file, 'bad']]
  if (kind === 'reextract') return [['Candidates updated', s.updated], ['Failed', s.failed, 'bad'], ['No CV file', s.no_cv_file, 'bad']]
  return []
}

function stateOf(task) {
  if (!task) return { tone: 'idle', label: 'Idle' }
  if (task.cancel_requested && ACTIVE.includes(task.status)) return { tone: 'amber', label: 'Stopping' }
  if (task.status === 'paused') return { tone: 'amber', label: 'Paused' }
  if (task.pause_requested) return { tone: 'amber', label: 'Pausing' }
  if (task.status === 'queued') return { tone: 'blue', label: 'Waiting' }
  return { tone: 'blue', label: 'Running' }
}

function StatusPill({ task }) {
  const { tone, label } = stateOf(task)
  return (
    <span className={`pl-pill pl-pill-${tone}`}>
      <span className="pl-dot" aria-hidden="true" />
      {task ? `${label} · ${NAMES[task.kind] || task.kind}` : label}
    </span>
  )
}

function Numbers({ items }) {
  const shown = items.filter(([, value]) => value !== undefined && value !== null)
  if (!shown.length) return null
  return (
    <div className="pl-numbers">
      {shown.map(([label, value, bad]) => (
        <div key={label} className={`pl-number ${bad && value ? 'pl-number-bad' : ''}`}>
          <strong>{fmt(value)}</strong>
          <span>{label}</span>
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
    <div className="pl-options">
      <label className="pl-option">
        <span>Mailbox</span>
        <select value={value.mailboxId || ''} onChange={(e) => {
          const next = mailboxes.find((m) => m.id === Number(e.target.value))
          onChange({ ...value, mailboxId: next?.id, folder: next?.default_folder || 'INBOX' })
        }}>
          {mailboxes.map((m) => <option key={m.id} value={m.id}>{m.name} ({m.email})</option>)}
        </select>
      </label>
      <label className="pl-option">
        <span>Folder {loading && <span className="muted">· loading…</span>}</span>
        <select value={value.folder} onChange={(e) => onChange({ ...value, folder: e.target.value })}>
          {folderChoices.map((f) => <option key={f} value={f}>{f}</option>)}
        </select>
        {folderError && <span className="pl-option-error">Couldn't list folders: {folderError.message}</span>}
      </label>
      <div className="pl-option pl-option-wide" role="radiogroup" aria-label="Which mail">
        <span>Which mail</span>
        <label className="pl-radio">
          <input type="radio" checked={value.period === 'new'} onChange={() => onChange({ ...value, period: 'new' })} />
          New mail since the last fetch
          <span className="muted small">{lastFetch ? ` (last fetched ${formatDateTime(lastFetch)})` : ' (never fetched: reads all mail)'}</span>
        </label>
        <label className="pl-radio">
          <input type="radio" checked={value.period === 'range'} onChange={() => onChange({ ...value, period: 'range' })} />
          A date range
        </label>
        {value.period === 'range' && (
          <div className="date-range pl-dates">
            <span>From</span>
            <input type="date" aria-label="From date" value={value.since} onChange={(e) => onChange({ ...value, since: e.target.value })} />
            <span>to</span>
            <input type="date" aria-label="To date" value={value.until} min={value.since || undefined}
              onChange={(e) => onChange({ ...value, until: e.target.value })} />
            {value.since && value.until && <span className="muted small">{dmy(value.since)} to {dmy(value.until)}, both days included</span>}
          </div>
        )}
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

function ModePicker({ mode, setMode }) {
  return (
    <div className="pl-modes" role="radiogroup" aria-label="What to run">
      {MODES.map((m) => (
        <button key={m.kind} type="button" role="radio" aria-checked={mode === m.kind}
          className={`pl-mode ${mode === m.kind ? 'is-selected' : ''}`} onClick={() => setMode(m.kind)}>
          <strong>{m.title}</strong>
          <span>{m.text}</span>
        </button>
      ))}
    </div>
  )
}

function LiveRun({ task, onControl, busy, confirming, onConfirm }) {
  const [, tick] = useState(0)
  useEffect(() => {
    const timer = setInterval(() => tick((n) => n + 1), 1000)
    return () => clearInterval(timer)
  }, [])

  const percent = task.total ? Math.min(100, Math.round((100 * task.done) / task.total)) : null
  const unit = task.kind === 'fetch' || task.kind === 'run' ? 'mails' : task.kind === 'reextract' ? 'candidates' : 'files'
  const state = stateOf(task)
  const stopping = task.cancel_requested
  const paused = task.status === 'paused'

  return (
    <div className="stack">
      <div className="pl-live-head">
        <div>
          <h3>{NAMES[task.kind] || task.kind}</h3>
          {describeSource(task.params) && <p className="muted small">{describeSource(task.params)}</p>}
        </div>
        <span className="muted small num">Elapsed {duration(task.started_at || task.created_at)}</span>
      </div>

      <div className={`pl-bar pl-bar-${state.tone}`} role="progressbar" aria-valuemin={0} aria-valuemax={100}
        aria-valuenow={percent ?? undefined} aria-label="Progress">
        <div className={`pl-bar-fill ${percent === null ? 'is-unknown' : ''}`} style={{ width: `${percent ?? 100}%` }} />
      </div>
      <div className="pl-live-meta">
        <span className="num">
          {task.total ? `${fmt(task.done)} of ${fmt(task.total)} ${unit}` : task.status === 'queued' ? 'Waiting to start' : 'Starting…'}
        </span>
        {percent !== null && <strong className="num">{percent}%</strong>}
      </div>
      <p className="pl-current">
        {stopping ? 'Stopping after the current CV. Everything finished so far is kept.'
          : paused ? 'Paused. Nothing is being processed. Resume to carry on from here.'
            : task.pause_requested ? 'Pausing after the current CV…'
              : task.current || 'Starting…'}
      </p>

      {confirming && (
        <div className="pl-confirm" role="alertdialog" aria-label="Stop this run?">
          <span>Stop this run after the current CV? What's done so far is kept, and the next run carries on from there.</span>
          <div className="pl-actions">
            <Button variant="ghost" size="sm" onClick={() => onConfirm(false)}>Keep running</Button>
            <Button variant="danger-solid" size="sm" disabled={busy} onClick={() => { onConfirm(false); onControl('cancel') }}>
              Stop run
            </Button>
          </div>
        </div>
      )}
      {task.controllable === undefined && CONTROLLABLE.includes(task.kind) && (
        <Alert title="Pause and Cancel need a server restart">
          The Talent Pool server is running an older version. Restart it (python main.py serve) to use Pause and Cancel.
        </Alert>
      )}
    </div>
  )
}

// Always shown, so the controls are easy to find. Each is enabled only when it can be used.
function ControlBar({ task, lastRun, mode, busy, canQueue, onStart, onControl, onCancel, onRetry }) {
  const active = Boolean(task)
  const controllable = active && task.controllable === true && !task.cancel_requested
  const paused = active && (task.status === 'paused' || task.pause_requested)
  const retryable = !active && lastRun && PATHS[lastRun.kind]
  const why = !active ? 'No run in progress' : !controllable ? (task.cancel_requested ? 'Already stopping' : 'This run cannot be paused or stopped') : undefined
  const chosen = MODES.find((m) => m.kind === mode)
  return (
    <div className="pl-controlbar" role="toolbar" aria-label="Run controls">
      <Button disabled={(active && !canQueue) || busy}
        title={active ? (canQueue ? 'Starts when the current run finishes' : 'A run is already in progress') : undefined}
        onClick={() => onStart(chosen.path)}>
        <PlayIcon /> {active && canQueue ? 'Add to queue' : 'Start'}
      </Button>
      {paused ? (
        <Button variant="ghost" disabled={!controllable || busy} title={why} onClick={() => onControl('resume')}>
          <PlayIcon /> Resume
        </Button>
      ) : (
        <Button variant="ghost" disabled={!controllable || busy || task?.status === 'queued'} title={why} onClick={() => onControl('pause')}>
          <PauseIcon /> Pause
        </Button>
      )}
      <Button variant="danger" disabled={!controllable || busy} title={why} onClick={onCancel}>
        <StopIcon /> Cancel
      </Button>
      <Button variant="ghost" disabled={!retryable || busy}
        title={active ? 'Wait for the current run to finish' : !lastRun ? 'No earlier run' : undefined}
        onClick={() => onRetry(lastRun.kind)}>
        <RetryIcon /> {lastRun && !active ? `Retry: ${(NAMES[lastRun.kind] || lastRun.kind).toLowerCase()}` : 'Retry last run'}
      </Button>
    </div>
  )
}

const icon = { width: 15, height: 15, viewBox: '0 0 24 24', fill: 'none', stroke: 'currentColor', strokeWidth: 2, strokeLinecap: 'round', strokeLinejoin: 'round', 'aria-hidden': true }
const PlayIcon = () => <svg {...icon}><path d="M7 4v16l13-8z" fill="currentColor" stroke="none" /></svg>
const PauseIcon = () => <svg {...icon}><path d="M8 5v14M16 5v14" /></svg>
const StopIcon = () => <svg {...icon}><rect x="6" y="6" width="12" height="12" rx="1.5" fill="currentColor" stroke="none" /></svg>
const RetryIcon = () => <svg {...icon}><path d="M4 12a8 8 0 1 0 2.4-5.7M4 4v4h4" /></svg>

function Finished({ task, onRetry, onDone, busy }) {
  const name = NAMES[task.kind] || task.kind
  const heading = { done: `${name} finished`, cancelled: `${name} stopped`, error: `${name} failed` }[task.status]
  const result = task.result || {}
  const stoppedEarly = result.stopped_early || result.fetch?.stopped_early
  return (
    <div className="stack">
      <div className={`pl-outcome pl-outcome-${task.status}`}>
        <strong>{heading}</strong>
        {describeSource(task.params) && <span className="small">{describeSource(task.params)}</span>}
        <span className="small">
          {task.status === 'cancelled' && 'Stopped with Cancel. Everything done before that is kept. '}
          {task.status === 'error' && `${task.error} `}
          Took {duration(task.started_at || task.created_at, task.finished_at)}.
        </span>
      </div>
      {stoppedEarly && <Alert title="The mail fetch stopped early">{stoppedEarly}. Retry to carry on from there.</Alert>}
      <Numbers items={outcome(task.kind, result)} />
      <div className="pl-actions">
        {PATHS[task.kind] && (
          <Button disabled={busy} onClick={() => onRetry(task.kind)}>
            {task.status === 'done' ? 'Run again' : 'Retry'}
          </Button>
        )}
        <Button variant="ghost" onClick={onDone}>Done</Button>
      </div>
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

export default function TalentPipelinePage() {
  const [config, setConfig] = useState(null)
  const [status, setStatus] = useState(null)
  const [runs, setRuns] = useState([])
  const [task, setTask] = useState(null)
  const [finished, setFinished] = useState(null)
  const [mode, setMode] = useState('fetch')
  const [error, setError] = useState(null)
  const [busy, setBusy] = useState(false)
  const [confirming, setConfirming] = useState(false)
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

  const isFetch = mode === 'fetch' || mode === 'run'

  const talent = status?.talent
  const s3Problems = (talent?.s3_failed || 0) + (talent?.s3_missing || 0)
  const failedFiles = status?.failed || []
  const running = Boolean(task)
  // Retry repeats the last run that failed or was stopped (or just ended), from this page or the history.
  const lastRun = finished || runs.find((r) => r.status !== 'running') || null

  return (
    <div className="stack pl-page">
      <div className="page-header">
        <div>
          <h1 className="page-title">Pipeline</h1>
          <p className="muted small">Bring new CVs in from the mailbox and turn them into candidates.</p>
        </div>
        <StatusPill task={task} />
      </div>

      <ErrorAlert error={error} />

      <div className="pl-top">
        <section className="card pl-panel">
          <header className="pl-panel-head">
            <h2>{running ? 'Current run' : finished ? 'Last run' : 'Start a run'}</h2>
          </header>
          <div className="pl-panel-body stack">
            <ControlBar task={task} lastRun={lastRun} mode={mode} busy={busy} canQueue={isFetch}
              onStart={start} onControl={control}
              onCancel={() => setConfirming(true)} onRetry={(kind) => start(PATHS[kind], lastRun?.params || lastRun?.summary)} />
            {running ? (
              <>
                <LiveRun task={task} onControl={control} busy={busy} confirming={confirming} onConfirm={setConfirming} />
                {waiting.length > 0 && (
                  <div className="pl-waiting">
                    <strong className="small">Waiting to start ({waiting.length})</strong>
                    <ul>
                      {waiting.map((w) => (
                        <li key={w.id}>
                          <span className="small">{NAMES[w.kind] || w.kind}{describeSource(w.params) ? ` · ${describeSource(w.params)}` : ''}</span>
                          <Button variant="ghost" size="sm" onClick={() => cancelWaiting(w.id)}>Cancel</Button>
                        </li>
                      ))}
                    </ul>
                  </div>
                )}
                <details className="pl-queue-more">
                  <summary className="small">Queue another fetch</summary>
                  <div className="stack-sm">
                    <FetchOptions mailboxes={mailboxes} value={fetchOptions} onChange={setFetchOptions} />
                    <div><Button size="sm" disabled={busy} onClick={() => start('/fetch')}>Add to queue</Button></div>
                  </div>
                </details>
              </>
            ) : finished ? (
              <Finished task={finished} busy={busy} onDone={() => setFinished(null)}
                onRetry={(kind) => start(PATHS[kind], finished.params)} />
            ) : (
              <>
                <ModePicker mode={mode} setMode={setMode} />
                {isFetch && <FetchOptions mailboxes={mailboxes} value={fetchOptions} onChange={setFetchOptions} />}
              </>
            )}
          </div>
        </section>

        <section className="card pl-panel">
          <header className="pl-panel-head">
            <h2>Connections</h2>
            <Link className="small" to="/talent/admin/settings">Settings</Link>
          </header>
          <div className="pl-panel-body">
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
          </div>
        </section>
      </div>

      <section className="pl-tiles" aria-label="Status">
        <div className="pl-tile"><strong>{fmt(status?.cv_files)}</strong><span>CV files on disk</span></div>
        <div className="pl-tile"><strong>{fmt(status?.extracted_ok)}</strong><span>Extracted</span></div>
        <div className="pl-tile"><strong>{talent ? fmt(talent.candidates) : '—'}</strong><span>Candidates</span></div>
        <div className={`pl-tile ${talent?.needs_review ? 'is-warn' : ''}`}>
          <strong>{talent ? fmt(talent.needs_review) : '—'}</strong>
          <span>Need review</span>
          {talent?.needs_review > 0 && <Link className="small" to="/talent?needs_review=true">Review</Link>}
        </div>
        <div className={`pl-tile ${failedFiles.length ? 'is-bad' : ''}`}>
          <strong>{fmt(failedFiles.length)}</strong><span>Failed files</span>
        </div>
        <div className={`pl-tile ${s3Problems ? 'is-bad' : ''}`}>
          <strong>{talent ? fmt(s3Problems) : '—'}</strong>
          <span>Not in S3</span>
          {s3Problems > 0 && (
            <button type="button" className="link-btn small" disabled={running || busy} onClick={() => start(PATHS['s3-retry'])}>
              Retry uploads
            </button>
          )}
        </div>
      </section>

      <div className="pl-bottom">
        <section className="card pl-panel">
          <header className="pl-panel-head"><h2>Run history</h2></header>
          {runs.length === 0 ? (
            <p className="pl-panel-body muted">No runs yet. Runs started here appear in this list.</p>
          ) : (
            <ul className="pl-history">
              {runs.map((r) => {
                const tone = { done: 'green', cancelled: 'amber', error: 'red', running: 'blue' }[r.status]
                const label = { done: 'Finished', cancelled: 'Stopped', error: 'Failed', running: 'Running' }[r.status]
                const numbers = outcome(r.kind, r.summary).filter(([, v]) => v).slice(0, 3)
                return (
                  <li key={r.id} className="pl-run">
                    <div className="pl-run-main">
                      <strong>{NAMES[r.kind] || r.kind}</strong>
                      {describeSource(r.summary) && <span className="small">{describeSource(r.summary)}</span>}
                      <span className="muted small num">
                        {formatDateTime(r.started_at)} · {duration(r.started_at, r.finished_at)}
                      </span>
                      <span className="small">
                        {r.status === 'error' ? r.summary?.error
                          : numbers.length ? numbers.map(([label, v]) => `${fmt(v)} ${label.toLowerCase()}`).join(' · ')
                            : 'Nothing new'}
                      </span>
                    </div>
                    <div className="pl-run-side">
                      <Badge tone={tone}>{label}</Badge>
                      {(r.status === 'error' || r.status === 'cancelled') && PATHS[r.kind] && (
                        <Button variant="ghost" size="sm" disabled={running || busy} onClick={() => start(PATHS[r.kind])}>
                          Retry
                        </Button>
                      )}
                    </div>
                  </li>
                )
              })}
            </ul>
          )}
        </section>

        <section className="card pl-panel">
          <header className="pl-panel-head">
            <h2>Failed files {failedFiles.length > 0 && <Badge tone="red">{failedFiles.length}</Badge>}</h2>
            {failedFiles.length > 0 && (
              <Button variant="ghost" size="sm" disabled={running || busy} onClick={() => start('/extract')}>
                Retry failed files
              </Button>
            )}
          </header>
          {failedFiles.length === 0 ? (
            <p className="pl-panel-body muted">No failed files.</p>
          ) : (
            <ul className="pl-failed">
              {failedFiles.map((f) => (
                <li key={`${f.file}-${f.at}`}>
                  <span className="mono pl-failed-file">{f.file}</span>
                  <span className="pl-failed-error small">{f.error}</span>
                  <span className="muted small num">{formatDateTime(f.at)}</span>
                </li>
              ))}
            </ul>
          )}
        </section>
      </div>
    </div>
  )
}
