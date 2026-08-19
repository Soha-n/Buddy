/**
 * Blocks the app until the backend and Ollama are both reachable.
 *
 * "Not installed" and "installed but not running" are shown as separate states
 * because the user has to do something different in each case.
 */
import type { HealthResponse } from '../types/api'

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
          {error}
        </div>
        <ol className="fix-steps">
          <li>
            Open a terminal in <code>backend/</code>
          </li>
          <li>
            Activate the venv: <code>.\.venv\Scripts\Activate.ps1</code>
          </li>
          <li>
            Run: <code>uvicorn app.main:app --port 8000</code>
          </li>
        </ol>
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
