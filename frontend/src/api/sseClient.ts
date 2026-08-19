/**
 * Minimal SSE reader built on fetch + ReadableStream.
 *
 * The native EventSource cannot issue POST requests, and both of our streaming
 * endpoints need a JSON body, so the protocol is parsed by hand. Only the subset
 * of SSE the backend emits is supported: `event:` and `data:` lines separated by
 * a blank line.
 */
import type { SseEvent } from '../types/api'
import { apiUrl } from './config'

export class StreamAbortedError extends Error {
  constructor() {
    super('Stream aborted')
    this.name = 'StreamAbortedError'
  }
}

interface StreamOptions {
  signal?: AbortSignal
}

/** POST a JSON body and yield each SSE frame as it arrives. */
export async function* streamSse(
  path: string,
  body: unknown,
  options: StreamOptions = {},
): AsyncGenerator<SseEvent> {
  let response: Response
  try {
    response = await fetch(apiUrl(path), {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
      signal: options.signal,
    })
  } catch (err) {
    if (options.signal?.aborted) throw new StreamAbortedError()
    throw new Error(
      `Cannot reach the backend at ${apiUrl(path)}. Is the Python server running?`,
    )
  }

  if (!response.ok) {
    // Errors raised before streaming begins arrive as a normal JSON body.
    let detail = `Request failed with status ${response.status}`
    try {
      const payload = await response.json()
      if (payload?.detail) detail = String(payload.detail)
    } catch {
      /* keep the status-based message */
    }
    throw new Error(detail)
  }

  if (!response.body) {
    throw new Error('Response contained no body to stream.')
  }

  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''

  try {
    while (true) {
      const { done, value } = await reader.read()
      if (done) break

      buffer += decoder.decode(value, { stream: true })

      // Frames are separated by a blank line; the trailing partial frame stays
      // in the buffer until its terminator arrives.
      let boundary = buffer.indexOf('\n\n')
      while (boundary !== -1) {
        const rawFrame = buffer.slice(0, boundary)
        buffer = buffer.slice(boundary + 2)

        const parsed = parseFrame(rawFrame)
        if (parsed) yield parsed

        boundary = buffer.indexOf('\n\n')
      }
    }
  } catch (err) {
    if (options.signal?.aborted) throw new StreamAbortedError()
    throw err
  } finally {
    reader.cancel().catch(() => {
      /* the connection is already gone */
    })
  }
}

/** Parse one raw SSE frame into a typed event, or null if unusable. */
function parseFrame(raw: string): SseEvent | null {
  let eventName = 'message'
  const dataLines: string[] = []

  for (const line of raw.split('\n')) {
    if (line.startsWith('event:')) {
      eventName = line.slice(6).trim()
    } else if (line.startsWith('data:')) {
      dataLines.push(line.slice(5).trim())
    }
    // `:` comment lines and anything else are ignored.
  }

  if (dataLines.length === 0) return null

  try {
    const data = JSON.parse(dataLines.join('\n'))
    return { event: eventName, data } as SseEvent
  } catch {
    return null
  }
}
