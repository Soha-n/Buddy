/**
 * Floats Manage Models above whatever chat is open, with a backdrop and a
 * close affordance. The chat underneath stays mounted the whole time -
 * closing just unmounts this overlay and the chat is exactly as it was
 * (scroll position, draft text, any live stream), no re-fetch or re-render.
 */
import { useEffect } from 'react'

import type { InstalledModel } from '../types/api'
import { ManageModels } from './ManageModels'

interface ManageModelsOverlayProps {
  installed: InstalledModel[]
  activeModel: string | null
  onUse: (modelName: string) => void
  onModelsChanged: () => void
  onDownloadByName: (modelName: string) => void
  onClose: () => void
}

export function ManageModelsOverlay({ onClose, ...rest }: ManageModelsOverlayProps) {
  useEffect(() => {
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onClose()
    }
    document.addEventListener('keydown', handleKeyDown)
    return () => document.removeEventListener('keydown', handleKeyDown)
  }, [onClose])

  return (
    <div className="overlay-backdrop" onMouseDown={(e) => {
      if (e.target === e.currentTarget) onClose()
    }}>
      <div className="overlay-panel">
        <ManageModels {...rest} onClose={onClose} />
      </div>
    </div>
  )
}
