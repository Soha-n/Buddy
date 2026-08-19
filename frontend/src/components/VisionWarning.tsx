/**
 * Shown when an image is staged but the selected model cannot read images.
 *
 * Sending is blocked while this is up, so the warning must never be a dead end:
 * every way out is offered here. Switch targets are the vision models already
 * installed - nothing is recommended for download, since which models to install
 * is the user's call, not this component's.
 */
interface VisionWarningProps {
  currentModel: string
  installedVisionModels: string[]
  onSwitchModel: (model: string) => void
  onOpenManageModels: () => void
  onRemoveImages: () => void
}

export function VisionWarning({
  currentModel,
  installedVisionModels,
  onSwitchModel,
  onOpenManageModels,
  onRemoveImages,
}: VisionWarningProps) {
  const hasInstalled = installedVisionModels.length > 0

  return (
    <div className="vision-warning">
      <div className="vision-warning-text">
        <strong>{currentModel} can't read images</strong>
        {hasInstalled
          ? 'It only understands text, so the image would be ignored. Switch to a model that can see it.'
          : 'It only understands text, and none of your installed models can read images.'}
      </div>

      <div className="vision-warning-actions">
        {hasInstalled ? (
          installedVisionModels.map((model) => (
            <button
              key={model}
              type="button"
              className="vision-warning-button primary"
              onClick={() => onSwitchModel(model)}
            >
              Switch to {model}
            </button>
          ))
        ) : (
          <button
            type="button"
            className="vision-warning-button primary"
            onClick={onOpenManageModels}
          >
            Browse models
          </button>
        )}

        <button type="button" className="vision-warning-button" onClick={onRemoveImages}>
          Remove image
        </button>
      </div>
    </div>
  )
}
