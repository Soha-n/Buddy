/** Renders one tier (Best/Better/Good) from the shared /api/tiers fetch. */
import type { InstalledModel, TieredRecommendation } from '../../types/api'
import { ManageModelCard } from './ManageModelCard'

interface TierTabProps {
  recommendations: TieredRecommendation[]
  installed: InstalledModel[]
  activeModel: string | null
  onUse: (modelName: string) => void
  onDelete: (modelName: string) => void
  onDownloaded: () => void
  emptyMessage: string
}

export function TierTab({
  recommendations,
  installed,
  activeModel,
  onUse,
  onDelete,
  onDownloaded,
  emptyMessage,
}: TierTabProps) {
  const installedNames = new Set(installed.map((m) => m.name))

  if (recommendations.length === 0) {
    return <p className="muted">{emptyMessage}</p>
  }

  return (
    <div className="manage-grid">
      {recommendations.map((rec) => (
        <ManageModelCard
          key={rec.model.id}
          model={rec.model}
          fit={rec.fit}
          score={rec.score}
          installed={installedNames.has(rec.model.name)}
          isActive={rec.model.name === activeModel}
          onUse={onUse}
          onDelete={onDelete}
          onDownloaded={onDownloaded}
        />
      ))}
    </div>
  )
}
