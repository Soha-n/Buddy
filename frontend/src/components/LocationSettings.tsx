/**
 * Shows and corrects the location Buddy uses for "here" questions.
 *
 * Everything here is read from the user's own machine - nothing about where they
 * are is sent anywhere. The source is always stated, because a country inferred
 * from regional settings is far coarser than a city the user typed, and the two
 * should not look equally authoritative.
 *
 * Precise detection is a separate button because it raises an OS permission
 * dialog; that should never happen merely because a panel was opened.
 */
import { useEffect, useState } from 'react'

import { useLocation } from '../hooks/useLocation'

const SOURCE_LABEL: Record<string, string> = {
  manual: 'set by you',
  os_gps: 'from this device’s location service',
  os_region: 'from Windows regional settings — country only',
  unavailable: 'not set',
}

export function LocationSettings() {
  const { location, busy, error, detect, detectPrecise, save, reset } = useLocation()
  const [editing, setEditing] = useState(false)
  const [city, setCity] = useState('')

  // Seed the input with the current city whenever the edit form opens, so
  // correcting a near-miss is a small edit rather than retyping.
  useEffect(() => {
    if (editing) setCity(location?.city ?? '')
  }, [editing, location?.city])

  const known = location && location.source !== 'unavailable'

  return (
    <div className="location-settings">
      <div className="location-row">
        <div className="location-text">
          <span className="location-label">Location</span>
          <span className="location-value">
            {known ? location.label : 'Not set'}
            {location && (
              <span className="location-source">
                {' '}
                · {SOURCE_LABEL[location.source]}
              </span>
            )}
          </span>
          {location && (
            <span className="location-clock">
              {location.local_date} · {location.local_time}
              {location.timezone ? ` · ${location.timezone}` : ''}
            </span>
          )}
        </div>

        <div className="location-actions">
          {!known && (
            <button
              type="button"
              className="location-button"
              onClick={detect}
              disabled={busy}
            >
              {busy ? 'Detecting…' : 'Detect'}
            </button>
          )}
          {/* Only shown where the OS can actually provide coordinates, and only
              when we do not already have them. */}
          {location?.precise_available && location.source !== 'os_gps' && (
            <button
              type="button"
              className="location-button"
              onClick={detectPrecise}
              disabled={busy}
              title="Ask Windows for exact coordinates. Windows will ask your permission."
            >
              Use precise location
            </button>
          )}
          <button
            type="button"
            className="location-button"
            onClick={() => setEditing((v) => !v)}
            disabled={busy}
          >
            {editing ? 'Cancel' : known ? 'Change' : 'Set city'}
          </button>
          {location?.source === 'manual' && (
            <button
              type="button"
              className="location-button"
              onClick={reset}
              disabled={busy}
              title="Forget this and detect from the network again"
            >
              Reset
            </button>
          )}
        </div>
      </div>

      {editing && (
        <form
          className="location-form"
          onSubmit={(event) => {
            event.preventDefault()
            const trimmed = city.trim()
            if (!trimmed) return
            save(trimmed)
            setEditing(false)
          }}
        >
          <input
            type="text"
            value={city}
            onChange={(event) => setCity(event.target.value)}
            placeholder="City, e.g. Nagpur"
            autoFocus
          />
          <button type="submit" className="location-button primary" disabled={busy}>
            Save
          </button>
        </form>
      )}

      {error && <div className="location-error">{error}</div>}

      <p className="location-hint">
        Used when you ask about “here” — weather, local time, nearby things. All of
        it is read from this computer: your location is never sent to a
        geolocation service, and your date and time never leave the device.
      </p>
    </div>
  )
}
