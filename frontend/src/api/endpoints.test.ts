import { HttpResponse, http } from 'msw'
import { describe, expect, it } from 'vitest'
import { server } from '../test/server'
import { errorEnvelope, model, readyBad, readyOk, simulation } from '../test/fixtures'
import { getActiveModel, getHealth, getReady, getSimulation } from './endpoints'
import { ApiError, NetworkError, ResponseValidationError } from './errors'

describe('endpoints: success', () => {
  it('parses /health', async () => {
    server.use(http.get('*/health', () => HttpResponse.json({ status: 'ok' })))
    expect(await getHealth()).toEqual({ status: 'ok' })
  })

  it('parses /ready 200', async () => {
    server.use(http.get('*/ready', () => HttpResponse.json(readyOk)))
    expect((await getReady()).status).toBe('ready')
  })

  it('parses /api/v1/simulation', async () => {
    server.use(http.get('*/api/v1/simulation', () => HttpResponse.json(simulation)))
    const data = await getSimulation()
    expect(data.worker_online).toBe(true)
    expect(data.last_run?.id).toBe(7)
  })

  it('parses /api/v1/models/active and ignores unknown extra fields', async () => {
    server.use(http.get('*/api/v1/models/active', () => HttpResponse.json({ ...model, brand_new_field: 1 })))
    const data = await getActiveModel()
    expect(data?.version).toBe('xgb-test-1')
    expect(data).not.toHaveProperty('brand_new_field')
  })

  it('treats 404 on the active model as "no model", not an error', async () => {
    server.use(http.get('*/api/v1/models/active', () => HttpResponse.json(errorEnvelope('not_found', 'Active model not found'), { status: 404 })))
    expect(await getActiveModel()).toBeNull()
  })

  it('keeps a 404 with code http_error as an error (wrong route / incompatible backend)', async () => {
    server.use(http.get('*/api/v1/models/active', () => HttpResponse.json(errorEnvelope('http_error', 'Not Found'), { status: 404 })))
    const err = await getActiveModel().catch((e: unknown) => e)
    expect(err).toBeInstanceOf(ApiError)
    expect(err).toMatchObject({ status: 404, code: 'http_error' })
  })

  it('keeps a 404 without an envelope as an error', async () => {
    server.use(http.get('*/api/v1/models/active', () => new HttpResponse(null, { status: 404 })))
    await expect(getActiveModel()).rejects.toMatchObject({ status: 404, code: 'http_error' })
  })
})

describe('endpoints: /ready 503', () => {
  it('returns the ReadyOut body instead of throwing', async () => {
    server.use(http.get('*/ready', () => HttpResponse.json(readyBad, { status: 503 })))
    const data = await getReady()
    expect(data.status).toBe('not_ready')
    expect(data.checks.migrations).toBe('fail')
  })

  it('throws ApiError for a 503 that is not a ReadyOut body', async () => {
    server.use(http.get('*/ready', () => HttpResponse.json(errorEnvelope('unavailable', 'down'), { status: 503 })))
    await expect(getReady()).rejects.toMatchObject({ status: 503, code: 'unavailable' })
  })
})

describe('endpoints: errors', () => {
  it('maps the standard error envelope to ApiError', async () => {
    server.use(
      http.get('*/api/v1/simulation', () =>
        HttpResponse.json({ error: { code: 'boom', message: 'Something broke', details: { a: 1 } } }, { status: 500 }),
      ),
    )
    const err = await getSimulation().catch((e: unknown) => e)
    expect(err).toBeInstanceOf(ApiError)
    expect(err).toMatchObject({ status: 500, code: 'boom', message: 'Something broke', details: { a: 1 } })
  })

  it('falls back to a generic ApiError when the error body is not an envelope', async () => {
    server.use(http.get('*/health', () => new HttpResponse('<html>oops</html>', { status: 500 })))
    await expect(getHealth()).rejects.toMatchObject({ status: 500, code: 'http_error' })
  })

  it.each([502, 504])('classifies an empty-body gateway %i as unreachable, not a backend error', async (status) => {
    server.use(http.get('*/health', () => new HttpResponse(null, { status })))
    const err = await getHealth().catch((e: unknown) => e)
    expect(err).toBeInstanceOf(NetworkError)
    expect(err).not.toBeInstanceOf(ApiError)
  })

  it('classifies a gateway 502 on /ready as unreachable too', async () => {
    server.use(http.get('*/ready', () => new HttpResponse(null, { status: 502 })))
    await expect(getReady()).rejects.toBeInstanceOf(NetworkError)
  })

  it('does not treat ordinary 4xx responses as network failures', async () => {
    server.use(http.get('*/health', () => HttpResponse.json(errorEnvelope('forbidden', 'no'), { status: 403 })))
    const err = await getHealth().catch((e: unknown) => e)
    expect(err).toBeInstanceOf(ApiError)
    expect(err).not.toBeInstanceOf(NetworkError)
  })

  it('reports network failures separately from backend errors', async () => {
    server.use(http.get('*/health', () => HttpResponse.error()))
    const err = await getHealth().catch((e: unknown) => e)
    expect(err).toBeInstanceOf(NetworkError)
    expect(err).not.toBeInstanceOf(ApiError)
  })

  it('raises ResponseValidationError when the body breaks the contract', async () => {
    server.use(http.get('*/api/v1/simulation', () => HttpResponse.json({ worker_online: 'yes' })))
    const err = await getSimulation().catch((e: unknown) => e)
    expect(err).toBeInstanceOf(ResponseValidationError)
    expect((err as ResponseValidationError).issues.length).toBeGreaterThan(0)
  })
})
