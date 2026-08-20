/**
 * Resolves the backend base URL.
 *
 * This is deliberately the only place the API origin is decided.
 *
 * Packaged, the backend serves this bundle itself, so the correct origin is
 * simply the page's own. That matters because the backend binds an
 * OS-assigned port rather than a fixed one - a hardcoded 8000 collides with
 * whatever else the user runs - and a build-time constant cannot know which
 * port it got. An empty base yields relative URLs, which the browser resolves
 * against the current origin whatever the port turns out to be.
 *
 * In development the Vite dev server serves the page instead, so requests have
 * to be sent across to the backend explicitly.
 */

/** True when the page was served by the backend rather than the dev server. */
function isSameOriginBackend(): boolean {
  if (typeof window === 'undefined') return false
  // Tauri injects this into every page it loads; its presence means the shell
  // navigated us to the backend's own origin.
  if ('__TAURI_INTERNALS__' in window) return true
  // Served over file:// there is no origin to be relative to.
  if (window.location.protocol === 'file:') return false
  // Anything other than the Vite dev port is the backend serving the bundle.
  return window.location.port !== '5173'
}

const DEV_API_BASE = 'http://127.0.0.1:8000'

const explicitBase = import.meta.env.VITE_API_BASE as string | undefined

const apiBase: string = (
  explicitBase ?? (isSameOriginBackend() ? '' : DEV_API_BASE)
).replace(/\/$/, '')

export function apiUrl(path: string): string {
  return `${apiBase}${path.startsWith('/') ? path : `/${path}`}`
}

/**
 * Message for "the backend did not answer at all".
 *
 * Packaged, the shell starts the backend, so there is no command for the user
 * to run and naming one is actively misleading. In development there is.
 */
export function unreachableMessage(url: string): string {
  if (apiBase === '') {
    return 'Cannot reach the Buddy backend. Close Buddy and open it again.'
  }
  return `Cannot reach the backend at ${url}. Start it with: uvicorn app.main:app --port 8000`
}
