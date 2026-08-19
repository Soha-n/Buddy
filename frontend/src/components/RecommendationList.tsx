/** The top-3 grid plus a collapsible list of models that cannot run here. */
import type { ExcludedModel, InstalledModel, Recommendation } from '../types/api'
import { ModelCard } from './ModelCard'

interface RecommendationListProps {
  recommendations: Recommendation[]
  excluded: ExcludedModel[]
  installed: InstalledModel[]
  onSelect: (modelName: string) => void
}

export function RecommendationList({
  recommendations,
  excluded,
  installed,
  onSelect,
}: RecommendationListProps) {
  const installedNames = new Set(installed.map((m) => m.name))

  return (
    <div>
      <div className="model-grid">
        {recommendations.map((rec, index) => (
          <ModelCard
            key={rec.model.id}
            recommendation={rec}
            rank={index + 1}
            installed={installedNames.has(rec.model.name)}
            onSelect={onSelect}
          />
        ))}
      </div>

      {excluded.length > 0 && (
        <div className="excluded">
          <details>
            <summary>
              {excluded.length} model{excluded.length === 1 ? '' : 's'} ruled out for
              this machine
            </summary>
            <ul>
              {excluded.map((item) => (
                <li key={item.name}>
                  <code>{item.name}</code> — {item.reason}
                </li>
              ))}
            </ul>
          </details>
        </div>
      )}
    </div>
  )
}
