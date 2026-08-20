/**
 * Blocks the app until the backend and Ollama are both reachable.
 *
 * "Not installed" and "installed but not running" are shown as separate states
 * because the user has to do something different in each case.
 */
import type { HealthResponse } from '../types/api'

/**
 * True when running inside the desktop shell rather than a browser.
 *
 * Tauri injects __TAURI_INTERNALS__ into every page it loads, so this
 * distinguishes the packaged app from `npm run dev` without needing a build
 * flag that could drift out of sync with how the app was actually launched.
 */
const isPackaged =
  typeof window !== 'undefined' && '__TAURI_INTERNALS__' in window

interface HealthGateProps {
  health: HealthResponse | null
  loading: boolean
  error: string | null
  onRetry: () => void
  children: React.ReactNode
}

export function HealthGate({
  health,
  loading,
  error,
  onRetry,
  children,
}: HealthGateProps) {
  if (loading) {
    return (
      <div className="center-state">
        <div className="spinner" />
        <p className="muted">Connecting to the Buddy backend…</p>
      </div>
    )
  }

  // The Python API itself is unreachable.
  if (error) {
    return (
      <div className="center-state">
        <div className="error-box">
          <strong>Backend not reachable</strong>
          {/* The raw message names a dev command and a fixed port, neither of
              which means anything in the packaged app. */}
          {isPackaged ? null : error}
        </div>
        {isPackaged ? (
          // In the desktop app the backend is started by the shell, so there
          // is nothing for the user to run - restarting is the whole remedy.
          <p className="muted">
            Buddy could not start its backend. Close Buddy and open it again. If
            this keeps happening, reinstalling will restore any missing files.
          </p>
        ) : (
          <ol className="fix-steps">
            <li>
              Open a terminal in <code>backend/</code>
            </li>
            <li>
              Activate the venv: <code>.\.venv\Scripts\Activate.ps1</code>
            </li>
            <li>
              Run: <code>python run_server.py</code>
            </li>
          </ol>
        )}
        <button className="primary" onClick={onRetry}>
          Try again
        </button>
      </div>
    )
  }

  const ollama = health?.ollama

  if (ollama && !ollama.installed) {
    return (
      <div className="center-state">
        <div className="error-box">
          <strong>Ollama is not installed</strong>
          Buddy runs models through Ollama, so it needs to be installed first.
        </div>
        <p className="muted">
          Download it from <code>https://ollama.com/download</code>, then come back
          and retry.
        </p>
        <button className="primary" onClick={onRetry}>
          Check again
        </button>
      </div>
    )
  }

  if (ollama && !ollama.running) {
    return (
      <div className="center-state">
        <div className="error-box">
          <strong>Ollama is installed but not running</strong>
          {ollama.error ?? 'The Ollama service is not responding.'}
        </div>
        <p className="muted">
          Start it by launching the Ollama app, or run <code>ollama serve</code> in a
          terminal.
        </p>
        <button className="primary" onClick={onRetry}>
          Check again
        </button>
      </div>
    )
  }

  return <>{children}</>
}
