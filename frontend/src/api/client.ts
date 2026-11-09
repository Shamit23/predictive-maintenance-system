import type { ZodType } from 'zod'
import { ApiError, NetworkError, ResponseValidationError } from './errors'
import { errorEnvelopeSchema } from './schemas'

export interface RawResponse {
  status: number
  ok: boolean
  /** Parsed JSON, or `undefined` when the body was empty or not JSON. */
  body: unknown
}

/**
 * GET a same-origin path. Only relative paths are used (the Vite proxy forwards them to FastAPI), so no backend
 * address lives in application code. The only header is `Accept`, and `credentials: 'omit'` is explicit so the browser
 * never attaches cookies or HTTP auth to these public, read-only requests (fetch's default would send same-origin ones).
 */
export async function getRaw(path: string, signal?: AbortSignal): Promise<RawResponse> {
  let response: Response
  try {
    // Resolved against the page origin so it also works where fetch requires absolute URLs (tests).
    response = await fetch(new URL(path, window.location.origin), {
      method: 'GET',
      credentials: 'omit',
      signal,
      headers: { Accept: 'application/json' },
    })
  } catch (cause) {
    if (signal?.aborted) throw cause // cancellation is not a network failure
    throw new NetworkError('Could not reach the API', { cause })
  }
  // A gateway answered instead of the API (the Vite proxy sends an empty-body 502 when the backend is down). The body
  // is deliberately not inspected: it may be empty, so this must not depend on an error envelope.
  if (response.status === 502 || response.status === 504) {
    throw new NetworkError(`API unreachable (gateway returned HTTP ${response.status})`)
  }
  let body: unknown
  try {
    body = await response.json()
  } catch {
    body = undefined
  }
  return { status: response.status, ok: response.ok, body }
}

/** Turn a non-2xx response into an ApiError, using the `{error:{code,message,details}}` envelope when present. */
export function toApiError(res: RawResponse): ApiError {
  const parsed = errorEnvelopeSchema.safeParse(res.body)
  if (parsed.success) {
    const { code, message, details } = parsed.data.error
    return new ApiError(res.status, code, message, details)
  }
  return new ApiError(res.status, 'http_error', `HTTP ${res.status}`)
}

/** Validate a body against its schema, reporting paths and messages only (never the values). */
export function parseBody<T>(path: string, schema: ZodType<T>, body: unknown): T {
  const result = schema.safeParse(body)
  if (!result.success) {
    throw new ResponseValidationError(
      path,
      result.error.issues.map((i) => `${i.path.join('.') || '(root)'}: ${i.message}`),
    )
  }
  return result.data
}

export type QueryParams = Record<string, string | number | undefined>

/**
 * Append query parameters to a same-origin path. Undefined and empty values are omitted, so an unset filter is never
 * sent as `?risk=` (which the backend would reject).
 */
export function buildPath(path: string, params: QueryParams = {}): string {
  const search = new URLSearchParams()
  for (const [key, value] of Object.entries(params)) {
    if (value === undefined || value === '') continue
    search.set(key, String(value))
  }
  const query = search.toString()
  return query ? `${path}?${query}` : path
}

export async function getJson<T>(path: string, schema: ZodType<T>, signal?: AbortSignal): Promise<T> {
  const res = await getRaw(path, signal)
  if (!res.ok) throw toApiError(res)
  return parseBody(path, schema, res.body)
}
