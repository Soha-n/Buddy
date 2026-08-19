/**
 * Resolves the backend base URL.
 *
 * This is deliberately the only place the API origin is decided. Wrapping the
 * app in Tauri or Electron means changing VITE_API_BASE and nothing else.
 */
const DEFAULT_API_BASE = 'http://127.0.0.1:8000'

const apiBase: string = (
  import.meta.env.VITE_API_BASE ?? DEFAULT_API_BASE
).replace(/\/$/, '')

export function apiUrl(path: string): string {
  return `${apiBase}${path.startsWith('/') ? path : `/${path}`}`
}
