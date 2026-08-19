/** Search tab: instant local catalog filter plus an opt-in live library search. */
import { useModelSearch } from '../../hooks/useModelSearch'
import type { InstalledModel } from '../../types/api'
import { ManageModelCard } from './ManageModelCard'

interface AllTabProps {
  installed: InstalledModel[]
  activeModel: string | null
  onUse: (modelName: string) => void
  onDelete: (modelName: string) => void
  onDownloaded: () => void
  onDownloadByName: (modelName: string) => void
}

export function AllTab({
  installed,
  activeModel,
  onUse,
  onDelete,
  onDownloaded,
  onDownloadByName,
}: AllTabProps) {
  const {
    query,
    setQuery,
    catalogResults,
    libraryResult,
    searchingLibrary,
    loading,
    error,
    searchLibraryNow,
  } = useModelSearch()

  const installedNames = new Set(installed.map((m) => m.name))
  const catalogNames = new Set(catalogResults.map((m) => m.name))

  return (
    <div className="all-tab">
      <div className="search-row">
        <input
          type="text"
          className="search-input"
          placeholder="Search any model (e.g. mistral, coder, 70b)…"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
        />
      </div>

      {error && <div className="error-box small">{error}</div>}

      {loading ? (
        <p className="muted">Searching…</p>
      ) : catalogResults.length === 0 ? (
        <p className="muted">
          No matches in the curated catalog{query ? ` for "${query}"` : ''}.
        </p>
      ) : (
        <div className="manage-grid">
          {catalogResults.map((model) => (
            <ManageModelCard
              key={model.id}
              model={model}
              installed={installedNames.has(model.name)}
              isActive={model.name === activeModel}
              onUse={onUse}
              onDelete={onDelete}
              onDownloaded={onDownloaded}
              scoredCaption={false}
            />
          ))}
        </div>
      )}

      {query.trim() && (
        <div className="library-search-section">
          {!libraryResult ? (
            <button
              type="button"
              className="ghost"
              onClick={searchLibraryNow}
              disabled={searchingLibrary}
            >
              {searchingLibrary
                ? 'Searching ollama.com…'
                : 'Search the full Ollama library online'}
            </button>
          ) : (
            <>
              <p className="panel-title" style={{ marginTop: '1.5rem' }}>
                Live results from ollama.com
                {libraryResult.stale && (
                  <span className="stale-note"> (may be outdated)</span>
                )}
              </p>
              {libraryResult.source === 'catalog_only' ? (
                <p className="muted">
                  Live search is unavailable right now; showing local results only.
                </p>
              ) : libraryResult.entries.length === 0 ? (
                <p className="muted">No results found on ollama.com.</p>
              ) : (
                <div className="library-grid">
                  {libraryResult.entries
                    .filter((entry) => !catalogNames.has(entry.name))
                    .map((entry) => (
                      <div className="library-card" key={entry.name}>
                        <div className="model-name">{entry.name}</div>
                        {entry.description && (
                          <p className="model-desc">{entry.description}</p>
                        )}
                        <p className="library-caption">Not scored for your machine</p>
                        <button onClick={() => onDownloadByName(entry.name)}>
                          Download
                        </button>
                      </div>
                    ))}
                </div>
              )}
            </>
          )}
        </div>
      )}
    </div>
  )
}
