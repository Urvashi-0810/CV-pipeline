import { useCallback, useEffect, useMemo, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { TALENT_API_URL, talentApi } from '../api/talentClient'
import { DownloadIcon, FileTextIcon, SearchIcon } from '../components/icons'
import { Badge, Button, ErrorAlert, Pagination, formatDate, useDebounced } from '../components/ui'
import TalentCandidateDrawer from './TalentCandidateDrawer'
import TalentUploadModal from './TalentUploadModal'
import TypeaheadInput from './TypeaheadInput'

// URL keys are the API's own parameter names, so the list query is the URL's filters as they are.
const FILTER_KEYS = ['search', 'trade', 'location', 'skills', 'min_experience', 'max_experience', 'min_age', 'max_age',
  'received_from', 'received_to', 'source', 'needs_review', 'include_unknown']
const DEFAULT_LIMIT = 25
const COLUMNS = [
  ['name', 'Name'], ['trade', 'Trade'], ['passport', 'Passport No.'], ['experience', 'Experience', 'num'],
  ['age', 'Age', 'num'], ['location', 'Location'], ['received', 'Received On', 'num'],
]

function RangeInput({ value, onCommit, label, placeholder }) {
  const [text, setText] = useState(value)
  const debounced = useDebounced(text, 400)
  useEffect(() => setText(value), [value])
  useEffect(() => {
    if (debounced !== value) onCommit(debounced)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [debounced])
  return (
    <input className="talent-range-input" type="number" min="0" step="any" value={text} placeholder={placeholder}
      aria-label={label} onChange={(e) => setText(e.target.value)} />
  )
}

export default function TalentPoolPage() {
  const [params, setParams] = useSearchParams()
  const [searchText, setSearchText] = useState(params.get('search') || '')
  const debouncedSearch = useDebounced(searchText, 300)
  const [page, setPage] = useState({ items: [], total: 0 })
  const [sources, setSources] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [uploading, setUploading] = useState(false)

  const sort = params.get('sort') || 'received'
  const order = params.get('order') || 'desc'
  const limit = Number(params.get('limit')) || DEFAULT_LIMIT
  const offset = Number(params.get('offset')) || 0
  const openId = Number(params.get('candidate')) || null

  function updateParams(changes, { resetPage = true } = {}) {
    const next = new URLSearchParams(params)
    for (const [key, value] of Object.entries(changes)) {
      if (value === '' || value == null) next.delete(key)
      else next.set(key, String(value))
    }
    if (resetPage) next.delete('offset')
    setParams(next, { replace: true })
  }

  useEffect(() => {
    if ((params.get('search') || '') !== debouncedSearch) updateParams({ search: debouncedSearch })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [debouncedSearch])

  const query = useMemo(() => {
    const q = new URLSearchParams()
    for (const key of FILTER_KEYS) {
      const value = params.get(key)
      if (value) q.set(key, value)
    }
    q.set('sort', sort)
    q.set('order', order)
    q.set('limit', limit)
    q.set('offset', offset)
    return q
  }, [params, sort, order, limit, offset])

  const load = useCallback(async () => {
    setPage(await talentApi(`/talent/candidates?${query}`))
  }, [query])

  const loadSources = useCallback(() => {
    talentApi('/talent/filter-options').then((data) => setSources(data.sources)).catch(() => {})
  }, [])

  useEffect(() => {
    setLoading(true)
    load()
      .then(() => setError(null))
      .catch(setError)
      .finally(() => setLoading(false))
  }, [load])

  useEffect(loadSources, [loadSources])

  function refresh() {
    load().catch(setError)
    loadSources()
  }

  function toggleSort(key) {
    if (sort === key) updateParams({ order: order === 'desc' ? 'asc' : 'desc' })
    else updateParams({ sort: key, order: key === 'received' || key === 'experience' ? 'desc' : 'asc' })
  }

  const hasFilters = FILTER_KEYS.some((key) => params.get(key))
  const openCandidate = (id) => updateParams({ candidate: id }, { resetPage: false })

  return (
    <div className="stack">
      <div className="page-header">
        <div>
          <h1 className="page-title">Candidate List</h1>
          <p className="muted small">
            {page.total.toLocaleString('en-IN')} candidate{page.total === 1 ? '' : 's'}
            {hasFilters ? ' match these filters' : ''}
          </p>
        </div>
        <div className="page-actions">
          <a className="btn btn-ghost btn-sm" href={`${TALENT_API_URL}/api/candidates/download`}
            title="Download the candidate Excel">
            <DownloadIcon /> Download Excel
          </a>
          <Button size="sm" onClick={() => setUploading(true)}>
            + Upload CV
          </Button>
        </div>
      </div>

      <ErrorAlert error={error} />

      <section className="card filters-card">
        <div className="search-box">
          <SearchIcon />
          <input type="search" placeholder="Search name, passport, email, phone, trade, company, location, skills…"
            value={searchText} onChange={(e) => setSearchText(e.target.value)} aria-label="Search" />
        </div>
        <div className="filters-row">
          <TypeaheadInput field="trade" label="Trade" placeholder="Trade" value={params.get('trade') || ''}
            onCommit={(v) => updateParams({ trade: v })} />
          <TypeaheadInput field="location" label="Location" placeholder="Location" value={params.get('location') || ''}
            onCommit={(v) => updateParams({ location: v })} />
          <TypeaheadInput field="skills" label="Skills" placeholder="Skills (comma separated)"
            value={params.get('skills') || ''} onCommit={(v) => updateParams({ skills: v })} />
          <div className="date-range" role="group" aria-label="Experience range">
            <span>Experience</span>
            <RangeInput label="Minimum experience" placeholder="min" value={params.get('min_experience') || ''}
              onCommit={(v) => updateParams({ min_experience: v })} />
            <span>to</span>
            <RangeInput label="Maximum experience" placeholder="max" value={params.get('max_experience') || ''}
              onCommit={(v) => updateParams({ max_experience: v })} />
          </div>
          <div className="date-range" role="group" aria-label="Age range">
            <span>Age</span>
            <RangeInput label="Minimum age" placeholder="min" value={params.get('min_age') || ''}
              onCommit={(v) => updateParams({ min_age: v })} />
            <span>to</span>
            <RangeInput label="Maximum age" placeholder="max" value={params.get('max_age') || ''}
              onCommit={(v) => updateParams({ max_age: v })} />
          </div>
          <label className="talent-check small">
            <input type="checkbox" checked={params.get('include_unknown') !== 'false'}
              onChange={(e) => updateParams({ include_unknown: e.target.checked ? '' : 'false' })} />
            Include unknown experience/age
          </label>
        </div>
        <div className="filters-row">
          <div className="date-range" role="group" aria-label="Received date range">
            <span>Received</span>
            <input type="date" aria-label="Received from" value={params.get('received_from') || ''}
              onChange={(e) => updateParams({ received_from: e.target.value })} />
            <span>to</span>
            <input type="date" aria-label="Received to" value={params.get('received_to') || ''}
              onChange={(e) => updateParams({ received_to: e.target.value })} />
          </div>
          <select value={params.get('source') || ''} onChange={(e) => updateParams({ source: e.target.value })}
            aria-label="Source mailbox">
            <option value="">All sources</option>
            {sources.map((s) => (
              <option key={s.value} value={s.value}>
                {s.label} ({s.candidates})
              </option>
            ))}
          </select>
          <select value={params.get('needs_review') || ''} onChange={(e) => updateParams({ needs_review: e.target.value })}
            aria-label="Needs review">
            <option value="">Any review state</option>
            <option value="true">Needs review</option>
            <option value="false">Nothing to review</option>
          </select>
          {hasFilters && (
            <Button variant="ghost" size="sm" onClick={() => {
              setSearchText('')
              updateParams(Object.fromEntries(FILTER_KEYS.map((key) => [key, ''])))
            }}>
              Clear filters
            </Button>
          )}
        </div>
      </section>

      <section className="card">
        <div className="table-wrap">
          <table className="table talent-table">
            <thead>
              <tr>
                {COLUMNS.map(([key, label, align]) => (
                  <th key={key} className={align || ''} aria-sort={sort === key ? (order === 'asc' ? 'ascending' : 'descending') : undefined}>
                    <button className="sort-btn" onClick={() => toggleSort(key)}>
                      {label}{sort === key ? (order === 'asc' ? ' ▲' : ' ▼') : ''}
                    </button>
                  </th>
                ))}
                <th className="center">View CV</th>
              </tr>
            </thead>
            <tbody>
              {page.items.map((c) => (
                <tr key={c.id} className="clickable" onClick={() => openCandidate(c.id)}>
                  <td>
                    <span className="talent-name">{c.full_name || <span className="muted">Unnamed</span>}</span>
                    {c.needs_review && <> <Badge tone="amber">Needs review</Badge></>}
                    {c.cv_count > 1 && <span className="muted small"> · {c.cv_count} CVs</span>}
                  </td>
                  <td>{c.job_title || <span className="muted">—</span>}</td>
                  <td className="mono">{c.passport_number || <span className="muted">—</span>}</td>
                  <td className="num">{c.experience_years ?? <span className="muted">—</span>}</td>
                  <td className="num">{c.age ?? <span className="muted">—</span>}</td>
                  <td>{c.location || <span className="muted">—</span>}</td>
                  <td className="num">{formatDate(c.received_at)}</td>
                  <td className="center" onClick={(e) => e.stopPropagation()}>
                    <button className="icon-action" disabled={!c.latest_cv_viewable}
                      title={c.latest_cv_viewable ? 'View the latest CV' : 'The CV is not in S3'}
                      aria-label={`View CV of ${c.full_name || 'candidate'}`} onClick={() => openCandidate(c.id)}>
                      <FileTextIcon />
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {!loading && page.items.length === 0 && (
            <div className="empty">
              {hasFilters ? 'No candidates match these filters.' : 'No candidates yet. CVs appear here once they are extracted.'}
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

      {openId && (
        <TalentCandidateDrawer
          key={openId}
          candidateId={openId}
          onClose={() => updateParams({ candidate: '' }, { resetPage: false })}
          onChanged={refresh}
          onSwitch={openCandidate}
        />
      )}
      {uploading && (
        <TalentUploadModal
          onClose={() => setUploading(false)}
          onDone={refresh}
          onOpenCandidate={(id) => {
            setUploading(false)
            openCandidate(id)
          }}
        />
      )}
    </div>
  )
}
