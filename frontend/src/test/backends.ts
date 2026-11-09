import { HttpResponse, http } from 'msw'
import { errorEnvelope, page } from './fixtures'
import type { BackendOptions } from './fixtures'

/**
 * A backend stand-in for one paged list endpoint. It applies limit and offset like the real API, optionally filters
 * through `apply` (given the query parameters), and can fail or hold chosen requests (see `BackendOptions`).
 * Records the URL of every request it receives.
 */
export function listBackend<T>(
  path: string,
  all: T[],
  options: BackendOptions & { apply?: (rows: T[], params: URLSearchParams) => T[] } = {},
) {
  const requests: URL[] = []
  const handler = http.get(path, async ({ request }) => {
    const url = new URL(request.url)
    requests.push(url)
    const number = requests.length
    await options.hold?.(number)
    if (options.fail?.(number)) {
      return HttpResponse.json(errorEnvelope('internal_error', 'Server exploded'), { status: 500 })
    }
    const rows = options.apply ? options.apply(all, url.searchParams) : all
    const limit = Number(url.searchParams.get('limit'))
    const offset = Number(url.searchParams.get('offset'))
    return HttpResponse.json(page(rows.slice(offset, offset + limit), rows.length, limit, offset))
  })
  return { handler, requests, last: () => requests[requests.length - 1] as URL }
}
