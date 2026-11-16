import { useEffect, useRef, useState } from 'react'
import { talentApi } from '../api/talentClient'
import { Alert, Button, ErrorAlert, Field, Modal } from '../components/ui'

const ACCEPT = '.pdf,.doc,.docx,.png,.jpg,.jpeg,.tif,.tiff,.bmp,.webp'

// Manual upload: one upload is one person. Several files (CV, passport scan…) are merged into one PDF,
// then the full pipeline runs. The upload waits if a fetch or extract is already running.
export default function TalentUploadModal({ onClose, onDone, onOpenCandidate }) {
  const [files, setFiles] = useState([])
  const [task, setTask] = useState(null)
  const [skipped, setSkipped] = useState([])
  const [error, setError] = useState(null)
  const [busy, setBusy] = useState(false)
  const timer = useRef(null)

  useEffect(() => () => clearTimeout(timer.current), [])

  function poll(id) {
    timer.current = setTimeout(async () => {
      try {
        const next = await talentApi(`/tasks/${id}`)
        setTask(next)
        if (next.status === 'done' || next.status === 'error') {
          if (next.status === 'done') onDone()
          return
        }
      } catch (err) {
        setError(err)
        return
      }
      poll(id)
    }, 1000)
  }

  async function submit(event) {
    event.preventDefault()
    setBusy(true)
    setError(null)
    const form = new FormData()
    files.forEach((file) => form.append('files', file))
    try {
      const started = await talentApi('/cvs/upload', { method: 'POST', form })
      setSkipped(started.skipped_unsupported || [])
      setTask(started)
      poll(started.id)
    } catch (err) {
      setError(err)
    } finally {
      setBusy(false)
    }
  }

  const running = task && (task.status === 'queued' || task.status === 'running')
  const result = task?.status === 'done' ? task.result : null

  return (
    <Modal title="Upload CV" onClose={onClose}>
      {!task ? (
        <form onSubmit={submit} className="stack">
          <Alert tone="info" title="One upload is one candidate.">
            Choose every file for the same person (CV, passport scan, certificates). They are merged into one PDF.
            Upload different people separately.
          </Alert>
          <ErrorAlert error={error} />
          <Field label="Files" hint="PDF, Word or image files.">
            {(id) => (
              <input id={id} type="file" multiple accept={ACCEPT} onChange={(e) => setFiles([...e.target.files])} required />
            )}
          </Field>
          {files.length > 1 && <p className="muted small">{files.length} files will be merged into one CV.</p>}
          <div className="row-end">
            <Button type="button" variant="ghost" onClick={onClose}>
              Cancel
            </Button>
            <Button type="submit" disabled={busy || !files.length}>
              {busy ? 'Uploading…' : 'Upload and process'}
            </Button>
          </div>
        </form>
      ) : (
        <div className="stack">
          {skipped.length > 0 && (
            <Alert tone="info" title="Some files were skipped">
              Not a supported type: {skipped.join(', ')}
            </Alert>
          )}
          {running && (
            <div className="stack-sm">
              <p className="muted">{task.current || 'Processing…'}</p>
              <progress className="talent-progress" max={task.total || 1} value={task.done || 0} />
            </div>
          )}
          {task.status === 'error' && <Alert title="The upload could not be processed">{task.error}</Alert>}
          {result?.status === 'added' && (
            <Alert tone="success" title={result.message || 'CV added'}>
              {result.name || 'The candidate'} is now in the Talent Pool.
              {result.s3_status === 'Failed' && ' The S3 upload failed; retry it from the Pipeline screen.'}
            </Alert>
          )}
          {result?.status === 'duplicate' && (
            <Alert tone="info" title="This CV is already on file">
              It belongs to {result.name || 'an existing candidate'}. Nothing was added.
            </Alert>
          )}
          {result?.status === 'failed' && <Alert title="Extraction failed">{result.error}</Alert>}
          <ErrorAlert error={error} />
          <div className="row-end">
            {result?.candidate_id && (
              <Button variant="ghost" onClick={() => onOpenCandidate(result.candidate_id)}>
                Open candidate
              </Button>
            )}
            <Button onClick={onClose}>{running ? 'Close (keeps processing)' : 'Done'}</Button>
          </div>
        </div>
      )}
    </Modal>
  )
}
