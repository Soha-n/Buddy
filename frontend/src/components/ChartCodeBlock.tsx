/**
 * A Python code block with a Run button that renders the resulting chart.
 *
 * Running is always an explicit click. The code is model-written and executes
 * on the user's machine, so it is shown in full first and nothing happens until
 * the user asks for it - an auto-run chart would mean arbitrary generated code
 * executing as a side effect of reading a reply.
 *
 * The chart appears below the code rather than replacing it, so the user can see
 * what produced the numbers they are about to trust.
 */
import { useState } from 'react'

import type { ChartRunState } from '../hooks/useChartRunner'

interface ChartCodeBlockProps {
  code: string
  state: ChartRunState
  onRun: () => void
}

export function ChartCodeBlock({ code, state, onRun }: ChartCodeBlockProps) {
  const [copied, setCopied] = useState(false)
  const [collapsed, setCollapsed] = useState(false)

  const handleCopy = () => {
    void navigator.clipboard.writeText(code).then(() => {
      setCopied(true)
      setTimeout(() => setCopied(false), 1500)
    })
  }

  const { running, result } = state

  return (
    <div className="code-block chart-code-block">
      <div className="code-block-header">
        <span className="code-block-lang">python</span>

        <div className="chart-code-actions">
          <button
            type="button"
            className="code-block-copy"
            onClick={() => setCollapsed((v) => !v)}
          >
            {collapsed ? 'Show code' : 'Hide code'}
          </button>
          <button type="button" className="code-block-copy" onClick={handleCopy}>
            {copied ? 'Copied' : 'Copy'}
          </button>
          <button
            type="button"
            className="chart-run-button"
            onClick={onRun}
            disabled={running}
          >
            {running ? 'Running…' : result?.image_base64 ? 'Run again' : 'Run chart'}
          </button>
        </div>
      </div>

      {!collapsed && (
        <pre>
          <code className="language-python">{code}</code>
        </pre>
      )}

      {running && (
        <div className="chart-output running">
          <span className="attachment-chip-spinner" aria-hidden="true" />
          Running the code in an isolated process…
        </div>
      )}

      {result && !running && (
        <div className="chart-output">
          {result.image_base64 && (
            <img
              className="chart-image"
              src={`data:image/png;base64,${result.image_base64}`}
              alt="Generated chart"
            />
          )}

          {/* Printed output earns its place: a script often prints the totals
              behind the chart, which is the part worth quoting back. */}
          {result.stdout.trim() && (
            <pre className="chart-stdout">{result.stdout.trim()}</pre>
          )}

          {result.error && (
            <div className="chart-error">
              <strong>{result.ok ? 'Note' : "That didn't run"}</strong>
              {result.error}
            </div>
          )}

          {result.ok && result.duration_s > 0 && (
            <div className="chart-meta">Finished in {result.duration_s}s</div>
          )}
        </div>
      )}
    </div>
  )
}
