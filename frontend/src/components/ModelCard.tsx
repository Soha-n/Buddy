/** One recommended model, with the reasoning behind its score. */
import type { Recommendation } from '../types/api'

interface ModelCardProps {
  recommendation: Recommendation
  rank: number
  installed: boolean
  onSelect: (modelName: string) => void
}

const FIT_LABEL: Record<string, string> = {
  excellent: 'Excellent fit',
  good: 'Good fit',
  tight: 'Tight fit',
}

export function ModelCard({
  recommendation,
  rank,
  installed,
  onSelect,
}: ModelCardProps) {
  const { model, fit, reasoning, expected_speed, score } = recommendation
  const isTopPick = rank === 1

  return (
    <div className={`model-card${isTopPick ? ' top-pick' : ''}`}>
      {isTopPick && <span className="top-pick-flag">Best match</span>}

      <div className="model-card-head">
        <div>
          <div className="model-name">{model.name}</div>
          <div className="model-meta">
            {model.params_b}B params · {model.download_size_gb} GB download
          </div>
        </div>
        <span className={`badge ${fit}`}>{FIT_LABEL[fit] ?? fit}</span>
      </div>

      <p className="model-desc">{model.description}</p>

      <div className="model-stats">
        <span className="stat-chip">Score {score}</span>
        <span className="stat-chip">{expected_speed}</span>
        {installed && <span className="stat-chip">Already downloaded</span>}
      </div>

      <ul className="reason-list">
        {reasoning.map((reason) => (
          <li key={reason}>{reason}</li>
        ))}
      </ul>

      <div className="model-card-actions">
        <button
          className={isTopPick ? 'primary' : ''}
          onClick={() => onSelect(model.name)}
        >
          {installed ? 'Use this model' : 'Download & use'}
        </button>
      </div>
    </div>
  )
}
