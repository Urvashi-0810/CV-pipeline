// Fetch wrapper for the Talent Pool API (the cv_pipeline backend), which runs on its own port.
// - sends the Talent Pool login cookie (credentials: 'include', because it's another origin)
// - adds the Talent Pool CSRF token on every write
// - turns errors into TalentApiError objects, which the CRM's UI components already understand
//
// Separate from client.js: it's a different server with its own login, CSRF token and error types.

import { ApiError } from './client'

// Same host the CRM was opened from, port 8100, unless VITE_TALENT_API_URL says otherwise.
// Using the same host keeps the login cookie same-site, whether the CRM is opened by name or IP.
export const TALENT_API_URL = (
  import.meta.env.VITE_TALENT_API_URL || `${window.location.protocol}//${window.location.hostname}:8100`
).replace(/\/$/, '')

let csrfToken = null

export function setTalentCsrfToken(token) {
  csrfToken = token
}

export class TalentApiError extends ApiError {
  constructor(status, body) {
    // The older pipeline routes answer {"detail": "..."} or {"detail": {"message": ...}}.
    const detail = body?.detail
    const problem = body?.type
      ? body
      : { title: (typeof detail === 'string' ? detail : detail?.message) || undefined }
    super(status, problem)
  }

  is(slug) {
    return this.type === `urn:talent:problem:${slug}`
  }
}

const listeners = new Set()
// Lets the Talent Pool screens react to "logged out" (401) responses.
export function onTalentUnauthorized(fn) {
  listeners.add(fn)
  return () => listeners.delete(fn)
}

export async function talentApi(path, { method = 'GET', body, form, headers = {} } = {}) {
  const init = { method, credentials: 'include', headers: { Accept: 'application/json', ...headers } }
  if (form !== undefined) {
    init.body = form
  } else if (body !== undefined) {
    init.headers['Content-Type'] = 'application/json'
    init.body = JSON.stringify(body)
  }
  if (method !== 'GET' && csrfToken) {
    init.headers['X-CSRF-Token'] = csrfToken
  }

  let response
  try {
    response = await fetch(`${TALENT_API_URL}/api${path}`, init)
  } catch {
    throw new TalentApiError(0, {
      type: 'urn:talent:problem:unreachable',
      title: 'Cannot reach the Talent Pool server',
      detail: `Check that it is running at ${TALENT_API_URL}.`,
    })
  }

  if (response.status === 204) return null
  const isJson = (response.headers.get('content-type') || '').includes('json')
  const data = isJson ? await response.json() : null

  if (!response.ok) {
    const error = new TalentApiError(response.status, data)
    if (response.status === 401 && error.is('not-authenticated')) {
      listeners.forEach((fn) => fn())
    }
    throw error
  }
  return data
}
