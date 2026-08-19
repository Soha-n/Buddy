/**
 * Manage Models view: hardware panel + Best/Better/Good/All tabs.
 * Replaces today's one-shot onboarding flow as a permanent, revisitable page.
 */
import { useState } from 'react'

import { deleteModel } from '../api/client'
import { useTieredModels } from '../hooks/useTieredModels'
import { AllTab } from './manage/AllTab'
import { TierTab } from './manage/TierTab'
import { SpecsPanel } from './SpecsPanel'
import type { InstalledModel } from '../types/api'

type Tab = 'best' | 'better' | 'good' | 'all'

const TABS: { id: Tab; label: string }[] = [
  { id: 'best', label: 'Best' },
  { id: 'better', label: 'Better' },
  { id: 'good', label: 'Good' },
  { id: 'all', label: 'All' },
]

interface ManageModelsProps {
  installed: InstalledModel[]
  activeModel: string | null
  onUse: (modelName: string) => void
  onModelsChanged: () => void
  onDownloadByName: (modelName: string) => void
  onClose: () => void
}

export function ManageModels({
  installed,
  activeModel,
  onUse,
  onModelsChanged,
  onDownloadByName,
  onClose,
}: ManageModelsProps) {
  const [tab, setTab] = useState<Tab>('best')
  const [specsExpanded, setSpecsExpanded] = useState(false)
  const { data, loading, error, rescan } = useTieredModels(true)

  const handleDelete = async (modelName: string) => {
    if (!window.confirm(`Delete ${modelName}? This frees disk space immediately.`)) {
      return
    }
    try {
      await deleteModel(modelName)
      onModelsChanged()
    } catch (err) {
      window.alert(err instanceof Error ? err.message : 'Failed to delete model')
    }
  }

  return (
    <div className="manage-models">
      <div className="manage-header">
        <div>
          <h2 className="section-heading">Manage models</h2>
          <p className="section-sub">
            Best/Better/Good are ranked for your machine; All lets you search anything.
          </p>
        </div>
        <button
          type="button"
          className="manage-close-button"
          onClick={onClose}
          aria-label="Close Manage models"
        >
          ✕
        </button>
      </div>

      <div className="panel hardware-summary">
        <button
          type="button"
          className="hardware-summary-toggle"
          onClick={() => setSpecsExpanded((v) => !v)}
        >
          <span className="panel-title" style={{ margin: 0 }}>
            Detected hardware
          </span>
          <span>{specsExpanded ? '▴' : '▾'}</span>
        </button>
        {specsExpanded && data && (
          <div style={{ marginTop: '0.9rem' }}>
            <SpecsPanel specs={data.specs} />
          </div>
        )}
        <div className="row" style={{ marginTop: '0.75rem' }}>
          <button className="ghost" onClick={rescan}>
            Re-scan hardware
          </button>
        </div>
      </div>

      <div className="tab-bar">
        {TABS.map((t) => (
          <button
            key={t.id}
            className={`tab-button${tab === t.id ? ' active' : ''}`}
            onClick={() => setTab(t.id)}
          >
            {t.label}
          </button>
        ))}
      </div>

      {loading && !data ? (
        <div className="center-state">
          <div className="spinner" />
          <p className="muted">Scoring models against your hardware…</p>
        </div>
      ) : error ? (
        <div className="error-box">
          <strong>Could not score models</strong>
          {error}
        </div>
      ) : data ? (
        <>
          {tab === 'best' && (
            <TierTab
              recommendations={data.best}
              installed={installed}
              activeModel={activeModel}
              onUse={onUse}
              onDelete={handleDelete}
              onDownloaded={onModelsChanged}
              emptyMessage="No models scored as an excellent fit for this machine yet."
            />
          )}
          {tab === 'better' && (
            <TierTab
              recommendations={data.better}
              installed={installed}
              activeModel={activeModel}
              onUse={onUse}
              onDelete={handleDelete}
              onDownloaded={onModelsChanged}
              emptyMessage="Nothing in this tier for your machine."
            />
          )}
          {tab === 'good' && (
            <TierTab
              recommendations={data.good}
              installed={installed}
              activeModel={activeModel}
              onUse={onUse}
              onDelete={handleDelete}
              onDownloaded={onModelsChanged}
              emptyMessage="Nothing in this tier for your machine."
            />
          )}
          {tab === 'all' && (
            <AllTab
              installed={installed}
              activeModel={activeModel}
              onUse={onUse}
              onDelete={handleDelete}
              onDownloaded={onModelsChanged}
              onDownloadByName={onDownloadByName}
            />
          )}
        </>
      ) : null}
    </div>
  )
}
