/** Progress indicator for the scan -> choose -> download -> chat flow. */
import type { Step } from '../types/steps'

interface StepIndicatorProps {
  current: Step
}

const ORDER: { id: Step; label: string }[] = [
  { id: 'scan', label: 'Scan' },
  { id: 'choose', label: 'Choose' },
  { id: 'download', label: 'Download' },
  { id: 'chat', label: 'Chat' },
]

export function StepIndicator({ current }: StepIndicatorProps) {
  const currentIndex = ORDER.findIndex((step) => step.id === current)

  return (
    <div className="steps">
      {ORDER.map((step, index) => {
        const state =
          index < currentIndex ? 'complete' : index === currentIndex ? 'active' : ''
        return (
          <div key={step.id} style={{ display: 'flex', alignItems: 'center' }}>
            {index > 0 && <div className="step-sep" />}
            <div className={`step ${state}`}>
              <span className="step-dot">{index < currentIndex ? '✓' : index + 1}</span>
              {step.label}
            </div>
          </div>
        )
      })}
    </div>
  )
}
