/**
 * Compact model card for Manage Models: name, specs, fit badge, one
 * context-aware action button, plus a delete icon when installed.
 */
import { useModelPull } from '../../hooks/useModelPull'
import type { CatalogModel, FitLevel } from '../../types/api'
import { formatEta, formatSpeed } from '../../utils/format'
import { ModelCapabilityBadge } from '../ModelCapabilityBadge'

interface ManageModelCardProps {
  model: CatalogModel
  fit?: FitLevel
  score?: number
  installed: boolean
  isActive: boolean
  onUse: (modelName: string) => void
  onDelete: (modelName: string) => void
  onDownloaded: () => void
  scoredCaption?: boolean
}

const FIT_LABEL: Record<string, string> = {
  excellent: 'Excellent fit',
  good: 'Good fit',
  tight: 'Tight fit',
}

export function ManageModelCard({
  model,
  fit,
  score,
  installed,
  isActive,
  onUse,
  onDelete,
  onDownloaded,
  scoredCaption = true,
}: ManageModelCardProps) {
  const pull = useModelPull()

  const handleClick = () => {
    if (installed) {
      onUse(model.name)
      return
    }
    if (pull.state === 'idle' || pull.state === 'error' || pull.state === 'cancelled') {
      pull.start(model.name)
    }
  }

  const isDownloading = pull.state === 'downloading'
  if (pull.state === 'done') {
    // Fire once; parent refreshes the installed list and re-renders this card
    // as installed=true on the next pass.
    onDownloaded()
  }

  const buttonLabel = installed
    ? isActive
      ? 'In use'
      : 'Use this model'
    : isDownloading
      ? 'Downloading…'
      : pull.state === 'error'
        ? 'Retry download'
        : 'Download'

  return (
    <div className={`manage-card${isActive ? ' active' : ''}`}>
      <div className="manage-card-head">
        <div>
          <div className="model-name">{model.name}</div>
          <div className="model-meta">
            {model.params_b}B params · {model.download_size_gb} GB
          </div>
          <div className="model-capability-row">
            <ModelCapabilityBadge
              modelName={model.name}
              installed={installed}
              catalogSaysVision={model.tags.includes('vision')}
            />
          </div>
        </div>
        {fit && <span className={`badge ${fit}`}>{FIT_LABEL[fit] ?? fit}</span>}
      </div>

      <p className="model-desc">{model.description}</p>

      <p className="model-ram-note">
        Uses ~{model.recommended_ram_gb} GB of RAM while running
        {model.min_vram_gb > 0 ? ` (or ${model.min_vram_gb} GB VRAM on GPU)` : ''}.
      </p>

      {score !== undefined && scoredCaption && (
        <div className="model-stats">
          <span className="stat-chip">Score {score}</span>
        </div>
      )}

      {isDownloading && (
        <div className="manage-card-progress">
          <div className="progress-track thin">
            <div
              className={`progress-fill${pull.progress?.percent == null ? ' indeterminate' : ''}`}
              style={
                pull.progress?.percent == null
                  ? undefined
                  : { width: `${pull.progress.percent}%` }
              }
            />
          </div>
          <div className="progress-meta small">
            <span>{formatSpeed(pull.progress?.speed_bps ?? null)}</span>
            <span>{formatEta(pull.progress?.eta_s ?? null)}</span>
          </div>
        </div>
      )}

      {pull.state === 'error' && pull.error && (
        <div className="manage-card-error">{pull.error}</div>
      )}

      <div className="manage-card-actions">
        <button
          className={installed && !isActive ? 'primary' : ''}
          onClick={handleClick}
          disabled={isDownloading || (installed && isActive)}
        >
          {buttonLabel}
        </button>
        {installed && (
          <button
            type="button"
            className="icon-button danger"
            title="Delete model"
            aria-label="Delete model"
            onClick={() => onDelete(model.name)}
          >
            🗑
          </button>
        )}
      </div>
    </div>
  )
}
