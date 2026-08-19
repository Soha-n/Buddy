/**
 * "Text only" / "Text + images" label for a model.
 *
 * Which source is trusted depends on whether the model is installed:
 *
 * - Installed: Ollama's own `capabilities` array, via /api/capabilities. This is
 *   authoritative - it reflects the actual model file on disk.
 * - Not installed: the catalog's `vision` tag, which is the only signal
 *   available before a download. Marked as a prediction by the tooltip, since a
 *   catalog entry is a claim about a model rather than a reading of one.
 *
 * Never guesses from the tag name. Substrings like "vl", "llava" or "vision"
 * catch some multimodal models and miss others, and a wrong label here sends the
 * user to a model that cannot do what the badge promised.
 */
import { useVisionCheck } from '../hooks/useVisionCheck'

interface ModelCapabilityBadgeProps {
  modelName: string
  installed: boolean
  /** Catalog `vision` tag, used only when the model is not installed yet. */
  catalogSaysVision?: boolean
}

export function ModelCapabilityBadge({
  modelName,
  installed,
  catalogSaysVision,
}: ModelCapabilityBadgeProps) {
  // Querying only for installed models keeps this from firing an /api/show
  // request per card for every model in the catalog.
  const { supportsVision } = useVisionCheck(installed ? modelName : null)

  if (installed) {
    // null means the answer has not arrived yet. Rendering nothing beats
    // rendering "Text only" and correcting it a moment later.
    if (supportsVision === null) return null
    return supportsVision ? (
      <span className="capability-badge vision" title="Can read images you attach">
        Text + images
      </span>
    ) : (
      <span
        className="capability-badge text-only"
        title="Cannot read images - attach an image and Buddy will ask you to switch"
      >
        Text only
      </span>
    )
  }

  if (catalogSaysVision === undefined) return null

  return catalogSaysVision ? (
    <span
      className="capability-badge vision predicted"
      title="Expected to read images, confirmed once installed"
    >
      Text + images
    </span>
  ) : (
    <span
      className="capability-badge text-only predicted"
      title="Expected to be text only, confirmed once installed"
    >
      Text only
    </span>
  )
}
