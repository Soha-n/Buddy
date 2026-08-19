/** Bottom-of-composer model picker, like Claude's — installed models only. */
import { useEffect, useRef, useState } from 'react'

import type { InstalledModel } from '../types/api'
import { ModelCapabilityBadge } from './ModelCapabilityBadge'

interface ModelSwitcherProps {
  currentModel: string
  installed: InstalledModel[]
  loading: boolean
  tokensPerSec: number | null
  onChange: (model: string) => void
  onBrowseMore: () => void
  disabled?: boolean
}

export function ModelSwitcher({
  currentModel,
  installed,
  loading,
  tokensPerSec,
  onChange,
  onBrowseMore,
  disabled,
}: ModelSwitcherProps) {
  const [open, setOpen] = useState(false)
  const rootRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!open) return
    const handleClick = (event: MouseEvent) => {
      if (rootRef.current && !rootRef.current.contains(event.target as Node)) {
        setOpen(false)
      }
    }
    document.addEventListener('mousedown', handleClick)
    return () => document.removeEventListener('mousedown', handleClick)
  }, [open])

  return (
    <div className="model-switcher" ref={rootRef}>
      <button
        type="button"
        className="model-switcher-trigger"
        onClick={() => setOpen((v) => !v)}
        disabled={disabled}
      >
        {loading ? (
          <span className="model-switcher-spinner" aria-hidden="true" />
        ) : (
          <span className="model-switcher-dot" aria-hidden="true" />
        )}
        <span className="model-switcher-name">{currentModel}</span>
        {!loading && tokensPerSec != null && (
          <span className="model-switcher-speed">{tokensPerSec} tok/s</span>
        )}
        {loading && <span className="model-switcher-status">Connecting…</span>}
        <span className="model-switcher-caret">{open ? '▴' : '▾'}</span>
      </button>

      {open && (
        <div className="model-switcher-menu">
          {installed.map((model) => (
            <button
              key={model.name}
              type="button"
              className={`model-switcher-item${model.name === currentModel ? ' active' : ''}`}
              onClick={() => {
                onChange(model.name)
                setOpen(false)
              }}
            >
              <span className="model-switcher-item-name">{model.name}</span>
              {/* Whether a model can see is the thing most likely to make the
                  user pick a different one, so it belongs in the picker itself
                  rather than only in Manage Models. */}
              <ModelCapabilityBadge modelName={model.name} installed />
              {model.name === currentModel && <span className="check">✓</span>}
            </button>
          ))}
          <div className="model-switcher-divider" />
          <button
            type="button"
            className="model-switcher-item browse"
            onClick={() => {
              onBrowseMore()
              setOpen(false)
            }}
          >
            Manage models…
          </button>
        </div>
      )}
    </div>
  )
}
