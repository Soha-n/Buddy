/** Debounced search for the "All" tab: instant local filter + optional live fetch. */
import { useEffect, useRef, useState } from 'react'

import { searchCatalog, searchLibrary } from '../api/client'
import type { CatalogModel, LibrarySearchResponse } from '../types/api'

const DEBOUNCE_MS = 150

interface UseModelSearchResult {
  query: string
  setQuery: (value: string) => void
  catalogResults: CatalogModel[]
  libraryResult: LibrarySearchResponse | null
  searchingLibrary: boolean
  loading: boolean
  error: string | null
  searchLibraryNow: () => void
}

export function useModelSearch(): UseModelSearchResult {
  const [query, setQuery] = useState('')
  const [catalogResults, setCatalogResults] = useState<CatalogModel[]>([])
  const [libraryResult, setLibraryResult] = useState<LibrarySearchResponse | null>(null)
  const [searchingLibrary, setSearchingLibrary] = useState(false)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  useEffect(() => {
    setLibraryResult(null)

    if (debounceRef.current) clearTimeout(debounceRef.current)
    debounceRef.current = setTimeout(() => {
      let cancelled = false
      setLoading(true)
      setError(null)

      searchCatalog(query)
        .then((results) => {
          if (!cancelled) setCatalogResults(results)
        })
        .catch((err: unknown) => {
          if (!cancelled) {
            setError(err instanceof Error ? err.message : 'Search failed')
          }
        })
        .finally(() => {
          if (!cancelled) setLoading(false)
        })

      return () => {
        cancelled = true
      }
    }, DEBOUNCE_MS)

    return () => {
      if (debounceRef.current) clearTimeout(debounceRef.current)
    }
  }, [query])

  const searchLibraryNow = () => {
    const trimmed = query.trim()
    if (!trimmed) return
    setSearchingLibrary(true)
    searchLibrary(trimmed)
      .then(setLibraryResult)
      .catch((err: unknown) => {
        setError(err instanceof Error ? err.message : 'Live search failed')
      })
      .finally(() => setSearchingLibrary(false))
  }

  return {
    query,
    setQuery,
    catalogResults,
    libraryResult,
    searchingLibrary,
    loading,
    error,
    searchLibraryNow,
  }
}
