/** Live download progress for the selected model. */
import type { PullProgress } from '../types/api'
import type { PullState } from '../hooks/useModelPull'
import { formatBytes, formatEta, formatSpeed } from '../utils/format'

interface DownloadProgressProps {
  model: string
  state: PullState
  progress: PullProgress | null
  error: string | null
  onCancel: () => void
  onRetry: () => void
  onBack: () => void
}

export function DownloadProgress({
  model,
  state,
  progress,
  error,
  onCancel,
  onRetry,
  onBack,
}: DownloadProgressProps) {
  // Early frames ("pulling manifest") have no byte counts, so the bar has to run
  // indeterminate until real numbers arrive.
  const percent = progress?.percent ?? null
  const hasBytes = (progress?.total ?? 0) > 0

  if (state === 'error') {
    return (
      <div className="panel download-card">
        <p className="panel-title">Download failed</p>
        <div className="error-box">
          <strong>{model}</strong>
          {error ?? 'The download did not complete.'}
        </div>
        <p className="muted" style={{ marginTop: '0.9rem' }}>
          Ollama keeps partially downloaded data, so retrying resumes rather than
          starting over.
        </p>
        <div className="row" style={{ marginTop: '1rem' }}>
          <button className="primary" onClick={onRetry}>
            Retry download
          </button>
          <button onClick={onBack}>Pick another model</button>
        </div>
      </div>
    )
  }

  if (state === 'cancelled') {
    return (
      <div className="panel download-card">
        <p className="panel-title">Download cancelled</p>
        <p className="muted">
          <code>{model}</code> was not finished. Any data already downloaded is kept,
          so resuming is quick.
        </p>
        <div className="row" style={{ marginTop: '1rem' }}>
          <button className="primary" onClick={onRetry}>
            Resume download
          </button>
          <button onClick={onBack}>Pick another model</button>
        </div>
      </div>
    )
  }

  return (
    <div className="panel download-card">
      <p className="panel-title">Downloading model</p>

      <div className="row" style={{ justifyContent: 'space-between' }}>
        <div>
          <div className="model-name">{model}</div>
          <div className="model-meta">
            {hasBytes
              ? `${formatBytes(progress?.completed ?? 0)} of ${formatBytes(progress?.total ?? 0)}`
              : 'Preparing download…'}
          </div>
        </div>
        <div className="progress-percent">
          {percent === null ? '—' : `${Math.floor(percent)}%`}
        </div>
      </div>

      <div className="progress-track">
        <div
          className={`progress-fill${percent === null ? ' indeterminate' : ''}`}
          style={percent === null ? undefined : { width: `${percent}%` }}
        />
      </div>

      <div className="progress-meta">
        <span>{formatSpeed(progress?.speed_bps ?? null)}</span>
        <span>
          {progress?.eta_s != null ? `${formatEta(progress.eta_s)} remaining` : ''}
        </span>
      </div>

      {progress?.status && <div className="progress-status">{progress.status}</div>}

      <div className="row" style={{ marginTop: '1.1rem' }}>
        <button onClick={onCancel} disabled={state !== 'downloading'}>
          Cancel
        </button>
      </div>
    </div>
  )
}
