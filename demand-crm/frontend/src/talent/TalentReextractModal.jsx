import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { talentApi } from '../api/talentClient'
import { Alert, Button, ErrorAlert, Modal } from '../components/ui'

// Re-extract the candidates the list's filters match, to fill the client's fields for older CVs.
// It costs one AI call per candidate, so the count is shown before anything starts.
export default function TalentReextractModal({ filters, onClose }) {
  const [onlyMissing, setOnlyMissing] = useState(!filters.has('missing_client_fields'))
  const [count, setCount] = useState(null)
  const [error, setError] = useState(null)
  const [busy, setBusy] = useState(false)
  const [started, setStarted] = useState(null)

  const query = new URLSearchParams(filters)
  if (onlyMissing) query.set('missing_client_fields', 'true')

  useEffect(() => {
    setCount(null)
    talentApi(`/talent/reextract/count?${query}`).then((data) => setCount(data.candidates)).catch(setError)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [onlyMissing])

  async function start() {
    setBusy(true)
    setError(null)
    try {
      setStarted(await talentApi(`/talent/reextract?${query}`, { method: 'POST' }))
    } catch (err) {
      setError(err)
    } finally {
      setBusy(false)
    }
  }

  return (
    <Modal title="Re-extract with AI" onClose={onClose}>
      {started ? (
        <div className="stack">
          <Alert tone="success" title={`Re-extracting ${count?.toLocaleString('en-IN')} candidate${count === 1 ? '' : 's'}.`}>
            It runs in the background. Watch it, pause it or stop it on the Pipeline page.
          </Alert>
          <div className="row-end">
            <Link className="btn btn-ghost" to="/talent/pipeline" onClick={onClose}>Open Pipeline</Link>
            <Button onClick={onClose}>Done</Button>
          </div>
        </div>
      ) : (
        <div className="stack">
          <p>
            The AI reads each candidate's newest CV again and fills the client's fields: gender, nationality,
            passport type and expiry, and the dated jobs that the experience columns are worked out from.
            Manual edits are kept unless the CV says something different.
          </p>
          <label className="talent-check">
            <input type="checkbox" checked={onlyMissing} onChange={(e) => setOnlyMissing(e.target.checked)} />
            Only candidates whose new fields are still empty
          </label>
          <ErrorAlert error={error} />
          <Alert tone="info" title={count === null ? 'Counting…' : `${count.toLocaleString('en-IN')} candidate${count === 1 ? '' : 's'} in the current filters`}>
            {count ? `That's ${count.toLocaleString('en-IN')} AI call${count === 1 ? '' : 's'}. Large numbers take hours and cost money.` : null}
          </Alert>
          <div className="row-end">
            <Button variant="ghost" onClick={onClose}>Cancel</Button>
            <Button disabled={busy || !count} onClick={start}>{busy ? 'Starting…' : 'Start re-extracting'}</Button>
          </div>
        </div>
      )}
    </Modal>
  )
}
