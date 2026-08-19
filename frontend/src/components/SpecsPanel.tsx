/** Shows the detected hardware plus any caveats about it. */
import type { SystemSpecs } from '../types/api'

interface SpecsPanelProps {
  specs: SystemSpecs
}

export function SpecsPanel({ specs }: SpecsPanelProps) {
  const { cpu, ram, gpus, disk, os } = specs

  const coreText = [
    cpu.physical_cores ? `${cpu.physical_cores} cores` : null,
    cpu.logical_cores ? `${cpu.logical_cores} threads` : null,
    cpu.max_clock_mhz ? `${(cpu.max_clock_mhz / 1000).toFixed(1)} GHz` : null,
  ]
    .filter(Boolean)
    .join(' · ')

  return (
    <div className="panel">
      <p className="panel-title">Detected hardware</p>
      <div className="spec-grid">
        <div className="spec-item">
          <div className="spec-label">Processor</div>
          <div className="spec-value">{cpu.name}</div>
          {coreText && <div className="spec-detail">{coreText}</div>}
        </div>

        <div className="spec-item">
          <div className="spec-label">Memory</div>
          <div className="spec-value">{ram.total_gb} GB RAM</div>
          {ram.available_gb !== null && (
            <div className="spec-detail">{ram.available_gb} GB free right now</div>
          )}
        </div>

        <div className="spec-item">
          <div className="spec-label">Graphics</div>
          {gpus.length === 0 ? (
            <div className="spec-value">None detected</div>
          ) : (
            gpus.map((gpu) => (
              <div key={gpu.name} style={{ marginBottom: '0.4rem' }}>
                <div className="spec-value">{gpu.name}</div>
                <div className="spec-detail">
                  {gpu.vram_gb === null
                    ? 'VRAM unknown'
                    : `${gpu.vram_gb} GB VRAM${gpu.vram_reliable ? '' : ' (reported value may be capped)'}`}
                </div>
              </div>
            ))
          )}
        </div>

        <div className="spec-item">
          <div className="spec-label">Disk (model store)</div>
          <div className="spec-value">{disk.free_gb} GB free</div>
          <div className="spec-detail">{disk.path}</div>
        </div>

        <div className="spec-item">
          <div className="spec-label">System</div>
          <div className="spec-value">
            {os.system} {os.release}
          </div>
          <div className="spec-detail">
            {os.machine} · build {os.version}
          </div>
        </div>
      </div>

      {specs.warnings.length > 0 && (
        <ul className="warning-list">
          {specs.warnings.map((warning) => (
            <li key={warning}>{warning}</li>
          ))}
        </ul>
      )}
    </div>
  )
}
