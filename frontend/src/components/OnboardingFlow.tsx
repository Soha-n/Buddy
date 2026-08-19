/**
 * First-run flow: scan -> choose -> download. Runs once, only when zero
 * models are installed; reachable again afterwards only via Manage Models.
 */
import { useCallback, useEffect, useState } from 'react'

import { useModelPull } from '../hooks/useModelPull'
import { useSystemSpecs } from '../hooks/useSystemSpecs'
import type { Step } from '../types/steps'
import { DownloadProgress } from './DownloadProgress'
import { RecommendationList } from './RecommendationList'
import { SpecsPanel } from './SpecsPanel'
import { StepIndicator } from './StepIndicator'

interface OnboardingFlowProps {
  onComplete: (model: string) => void
}

export function OnboardingFlow({ onComplete }: OnboardingFlowProps) {
  const [step, setStep] = useState<Step>('scan')
  const [selectedModel, setSelectedModel] = useState<string | null>(null)
  const { data, loading: specsLoading, error: specsError, rescan } = useSystemSpecs(true)
  const pull = useModelPull()

  useEffect(() => {
    if (step === 'scan' && data && !specsLoading) {
      setStep('choose')
    }
  }, [data, specsLoading, step])

  useEffect(() => {
    if (pull.state === 'done' && selectedModel) {
      onComplete(selectedModel)
    }
  }, [pull.state, selectedModel, onComplete])

  const handleSelect = useCallback(
    (modelName: string) => {
      setSelectedModel(modelName)
      setStep('download')
      pull.start(modelName)
    },
    [pull],
  )

  const handleRetryDownload = useCallback(() => {
    if (selectedModel) pull.start(selectedModel)
  }, [pull, selectedModel])

  const handleBackToChoose = useCallback(() => {
    pull.reset()
    setSelectedModel(null)
    setStep('choose')
  }, [pull])

  return (
    <div className="app onboarding">
      <header className="app-header">
        <div className="app-title">
          <img src="/logo.svg" alt="" className="app-title-mark" aria-hidden="true" />
          <div className="app-title-text">
            <h1>
              Buddy
              <span className="app-title-sub">private</span>
            </h1>
            <span>Local AI, matched to your machine</span>
          </div>
        </div>
        <StepIndicator current={step} />
      </header>

      <main className="app-body">
        {step === 'scan' && (
          <div className="center-state">
            {specsError ? (
              <>
                <div className="error-box">
                  <strong>Hardware scan failed</strong>
                  {specsError}
                </div>
                <button className="primary" onClick={rescan}>
                  Try again
                </button>
              </>
            ) : (
              <>
                <div className="spinner" />
                <p className="muted">
                  Checking your CPU, memory, graphics and free disk space…
                </p>
              </>
            )}
          </div>
        )}

        {step === 'choose' && data && (
          <div className="stack">
            <SpecsPanel specs={data.specs} />
            <div>
              <h2 className="section-heading">Best models for this machine</h2>
              <p className="section-sub">
                Ranked {data.catalog_size} models by how well they fit your memory,
                graphics and disk space. Pick one to download and start chatting.
              </p>
              <RecommendationList
                recommendations={data.recommendations}
                excluded={data.excluded}
                installed={[]}
                onSelect={handleSelect}
              />
            </div>
          </div>
        )}

        {step === 'download' && selectedModel && (
          <DownloadProgress
            model={selectedModel}
            state={pull.state}
            progress={pull.progress}
            error={pull.error}
            onCancel={pull.cancel}
            onRetry={handleRetryDownload}
            onBack={handleBackToChoose}
          />
        )}
      </main>
    </div>
  )
}
