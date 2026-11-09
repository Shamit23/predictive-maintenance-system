import { HttpResponse, http } from 'msw'
import { describe, expect, it } from 'vitest'
import { alertOut, engineDetail, engineSummary, errorEnvelope, modelsPage, page, predictionOut } from '../test/fixtures'
import { server } from '../test/server'
import { getEngine, listAlerts, listEngines, listModels, listPredictions } from './endpoints'
import { ApiError, NetworkError, ResponseValidationError } from './errors'

/** Records the URL of every request a handler receives. */
function record() {
  const urls: URL[] = []
  return { urls, capture: (request: Request) => urls.push(new URL(request.url)) }
}

describe('listModels', () => {
  it('parses a page and sends limit and offset', async () => {
    const { urls, capture } = record()
    server.use(http.get('*/api/v1/models', ({ request }) => (capture(request), HttpResponse.json(modelsPage))))
    const result = await listModels({ limit: 10, offset: 20 })
    expect(result.total).toBe(3)
    expect(result.items.map((m) => m.version)).toEqual(['xgb-test-2', 'xgb-test-1', 'xgb-test-0'])
    expect(urls[0]?.search).toBe('?limit=10&offset=20')
  })

  it('keeps unknown metrics shapes intact (free-form dict)', async () => {
    server.use(http.get('*/api/v1/models', () => HttpResponse.json(modelsPage)))
    const result = await listModels({ limit: 10, offset: 0 })
    expect(result.items[2]?.metrics).toEqual({})
    expect(result.items[0]?.activated_at).toBeNull()
  })

  it('rejects a body that breaks the contract', async () => {
    server.use(http.get('*/api/v1/models', () => HttpResponse.json({ items: [{ version: 1 }], total: 1, limit: 10, offset: 0 })))
    await expect(listModels({ limit: 10, offset: 0 })).rejects.toBeInstanceOf(ResponseValidationError)
  })
})

describe('listEngines', () => {
  it('omits unset filters entirely', async () => {
    const { urls, capture } = record()
    server.use(http.get('*/api/v1/engines', ({ request }) => (capture(request), HttpResponse.json(page([], 0, 25, 0)))))
    await listEngines({ limit: 25, offset: 0 })
    expect(urls[0]?.search).toBe('?limit=25&offset=0')
  })

  it('maps filters to the backend parameter names', async () => {
    const { urls, capture } = record()
    server.use(http.get('*/api/v1/engines', ({ request }) => (capture(request), HttpResponse.json(page([], 0, 25, 50)))))
    await listEngines({ search: '24', risk: 'critical', alertStatus: 'none', limit: 25, offset: 50 })
    const params = urls[0]?.searchParams
    expect(params?.get('search')).toBe('24')
    expect(params?.get('risk')).toBe('critical')
    expect(params?.get('alert_status')).toBe('none')
    expect(params?.get('limit')).toBe('25')
    expect(params?.get('offset')).toBe('50')
  })

  it('parses summaries including a missing prediction, a missing alert and both origins', async () => {
    const items = [
      engineSummary(1),
      engineSummary(2, { latest_prediction: null, risk_category: null, alert_status: null, alert_severity: null, origin: 'api', last_reading_at: null }),
    ]
    server.use(http.get('*/api/v1/engines', () => HttpResponse.json(page(items, 2, 25, 0))))
    const result = await listEngines({ limit: 25, offset: 0 })
    expect(result.items[0]?.origin).toBe('simulation')
    expect(result.items[1]).toMatchObject({ origin: 'api', latest_prediction: null, risk_category: null, alert_status: null })
  })

  it('rejects an unknown origin or risk value instead of guessing', async () => {
    server.use(http.get('*/api/v1/engines', () => HttpResponse.json(page([engineSummary(1, { origin: 'field' })], 1, 25, 0))))
    await expect(listEngines({ limit: 25, offset: 0 })).rejects.toBeInstanceOf(ResponseValidationError)
  })

  it('treats a gateway 502 as unreachable and a 500 envelope as a backend error', async () => {
    server.use(http.get('*/api/v1/engines', () => new HttpResponse(null, { status: 502 })))
    await expect(listEngines({ limit: 25, offset: 0 })).rejects.toBeInstanceOf(NetworkError)
    server.use(http.get('*/api/v1/engines', () => HttpResponse.json(errorEnvelope('internal_error', 'boom'), { status: 500 })))
    await expect(listEngines({ limit: 25, offset: 0 })).rejects.toMatchObject({ status: 500, code: 'internal_error' })
  })
})

describe('getEngine', () => {
  it('parses the detail response', async () => {
    server.use(http.get('*/api/v1/engines/24', () => HttpResponse.json(engineDetail)))
    const result = await getEngine(24)
    expect(result).toMatchObject({ id: 24, reading_count: 100, prediction_count: 100 })
  })

  it('leaves an unknown engine as a not_found ApiError for the page to show', async () => {
    server.use(http.get('*/api/v1/engines/999', () => HttpResponse.json(errorEnvelope('not_found', 'Engine 999 not found.'), { status: 404 })))
    const error = await getEngine(999).catch((e: unknown) => e)
    expect(error).toBeInstanceOf(ApiError)
    expect(error).toMatchObject({ status: 404, code: 'not_found' })
  })

  it.each([0, -3, 1.5, Number.NaN, Number.MAX_SAFE_INTEGER + 2])('rejects the invalid id %s without any request', async (id) => {
    // No handler is registered: an unhandled request would fail the test.
    await expect(getEngine(id)).rejects.toBeInstanceOf(RangeError)
  })
})

describe('listPredictions', () => {
  it('requests the engine path with order, limit and offset and parses points', async () => {
    const { urls, capture } = record()
    const items = [predictionOut(3, 40, 'warning'), predictionOut(2, 70, 'normal')]
    server.use(
      http.get('*/api/v1/engines/24/predictions', ({ request }) => (capture(request), HttpResponse.json(page(items, 250, 200, 0)))),
    )
    const result = await listPredictions(24, { order: 'desc', limit: 200, offset: 0 })
    expect(urls[0]?.pathname).toBe('/api/v1/engines/24/predictions')
    expect(urls[0]?.search).toBe('?order=desc&limit=200&offset=0')
    expect(result.total).toBe(250)
    expect(result.items).toHaveLength(2)
  })

  it('keeps several predictions for the same cycle (different model versions)', async () => {
    const items = [predictionOut(5, 40, 'warning', 'v2'), predictionOut(5, 42, 'warning', 'v1')]
    server.use(http.get('*/api/v1/engines/24/predictions', () => HttpResponse.json(page(items, 2, 200, 0))))
    const result = await listPredictions(24, { limit: 200, offset: 0 })
    expect(result.items.map((p) => [p.cycle, p.model_version])).toEqual([[5, 'v2'], [5, 'v1']])
  })

  it('rejects an invalid engine id and a bad risk value', async () => {
    await expect(listPredictions(0, { limit: 200, offset: 0 })).rejects.toBeInstanceOf(RangeError)
    server.use(
      http.get('*/api/v1/engines/24/predictions', () => HttpResponse.json(page([{ ...predictionOut(1, 5, 'normal'), risk_category: 'bad' }], 1, 200, 0))),
    )
    await expect(listPredictions(24, { limit: 200, offset: 0 })).rejects.toBeInstanceOf(ResponseValidationError)
  })
})

describe('listAlerts', () => {
  it('maps filters to backend parameter names and omits unset ones', async () => {
    const { urls, capture } = record()
    server.use(http.get('*/api/v1/alerts', ({ request }) => (capture(request), HttpResponse.json(page([alertOut(1)], 1, 25, 0)))))
    await listAlerts({ status: 'unresolved', severity: 'critical', engineId: 24, limit: 25, offset: 0 })
    const params = urls[0]?.searchParams
    expect(params?.get('status')).toBe('unresolved')
    expect(params?.get('severity')).toBe('critical')
    expect(params?.get('engine_id')).toBe('24')
    await listAlerts({ limit: 25, offset: 0 })
    expect(urls[1]?.search).toBe('?limit=25&offset=0')
  })

  it('parses resolved alerts with their timestamps and reason', async () => {
    const resolved = alertOut(2, {
      status: 'resolved', resolved_at: '2026-01-02T00:00:00Z', resolution_reason: 'recovered', acknowledged_at: '2026-01-01T12:00:00Z',
    })
    server.use(http.get('*/api/v1/alerts', () => HttpResponse.json(page([resolved], 1, 25, 0))))
    const result = await listAlerts({ limit: 25, offset: 0 })
    expect(result.items[0]).toMatchObject({ status: 'resolved', resolution_reason: 'recovered' })
  })

  it('never sends credentials', async () => {
    let headers: Headers | undefined
    server.use(http.get('*/api/v1/alerts', ({ request }) => ((headers = request.headers), HttpResponse.json(page([], 0, 25, 0)))))
    await listAlerts({ limit: 25, offset: 0 })
    expect(headers?.has('x-api-key')).toBe(false)
    expect(headers?.has('x-control-key')).toBe(false)
    expect(headers?.has('authorization')).toBe(false)
  })
})
