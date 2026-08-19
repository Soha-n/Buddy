/**
 * Whether the current model can read images, and what to switch to if not.
 *
 * Answers are cached per model in a module-level map. The composer consults this
 * on every render while an image is attached, and a model's capabilities cannot
 * change without it being re-pulled, so re-fetching would be pure waste.
 *
 * `supportsVision` stays null until the answer is known. That third state
 * matters: treating "not yet loaded" as "cannot see images" would flash a
 * warning and disable the send button for a model that turns out to be fine.
 */
import { useEffect, useState } from 'react'

import { checkVision } from '../api/client'
import type { VisionCheckResponse } from '../types/api'

const cache = new Map<string, VisionCheckResponse>()

interface UseVisionCheckResult {
  /** null while unknown - do not gate on it being false. */
  supportsVision: boolean | null
  installedVisionModels: string[]
}

export function useVisionCheck(model: string | null): UseVisionCheckResult {
  const [result, setResult] = useState<VisionCheckResponse | null>(
    model ? cache.get(model) ?? null : null,
  )

  useEffect(() => {
    if (!model) {
      setResult(null)
      return
    }

    const cached = cache.get(model)
    if (cached) {
      setResult(cached)
      return
    }

    // Cleared if the model changes before the request resolves, so a slow
    // answer for the previous model cannot land as the current one's.
    let active = true
    setResult(null)

    checkVision(model)
      .then((response) => {
        cache.set(model, response)
        if (active) setResult(response)
      })
      .catch(() => {
        // Leaving this unknown (null) rather than assuming "no vision" keeps a
        // backend hiccup from blocking an upload the model could have handled.
      })

    return () => {
      active = false
    }
  }, [model])

  return {
    supportsVision: result ? result.supports_vision : null,
    installedVisionModels: result?.installed_vision_models ?? [],
  }
}
