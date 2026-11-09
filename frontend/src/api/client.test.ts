import { describe, expect, it, vi } from 'vitest'
import { buildPath, getJson, getRaw } from './client'
import { getReady } from './endpoints'
import { healthSchema } from './schemas'

/** Replace `fetch` for one test with a stub that answers with this JSON body, and return the spy. */
function stubFetch(body: unknown = { status: 'ok' }, status = 200) {
  return vi
    .spyOn(globalThis, 'fetch')
    .mockImplementation(() => Promise.resolve(new Response(JSON.stringify(body), { status })))
}

describe('request configuration (what the browser is asked to send)', () => {
  it('makes a credential-free same-origin GET whose only header is Accept', async () => {
    const fetchSpy = stubFetch()
    await getJson('/health', healthSchema)
    expect(fetchSpy).toHaveBeenCalledOnce()
    const [input, init] = fetchSpy.mock.calls[0] as [URL, RequestInit]
    expect(String(input)).toBe(`${window.location.origin}/health`) // same origin, relative path resolved against it
    expect(init.method).toBe('GET')
    expect(init.credentials).toBe('omit') // never cookies or HTTP auth, even though fetch would send same-origin ones
    expect(init.headers).toEqual({ Accept: 'application/json' })
    expect(init.body).toBeUndefined()
    expect(init.mode).toBeUndefined()
  })

  it('applies the same configuration through getRaw and the /ready special case', async () => {
    const fetchSpy = stubFetch({ status: 'ready', checks: {} })
    await getRaw('/api/v1/alerts?limit=25')
    await getReady()
    for (const [, init] of fetchSpy.mock.calls as [URL, RequestInit][]) {
      expect(init).toMatchObject({ method: 'GET', credentials: 'omit', headers: { Accept: 'application/json' } })
    }
    expect(fetchSpy).toHaveBeenCalledTimes(2)
  })

  it('passes the abort signal through so a superseded request can be cancelled', async () => {
    const fetchSpy = stubFetch()
    const controller = new AbortController()
    await getJson('/health', healthSchema, controller.signal)
    expect((fetchSpy.mock.calls[0] as [URL, RequestInit])[1].signal).toBe(controller.signal)
  })
})

describe('buildPath', () => {
  it('returns the bare path without parameters', () => {
    expect(buildPath('/api/v1/engines')).toBe('/api/v1/engines')
    expect(buildPath('/api/v1/engines', {})).toBe('/api/v1/engines')
  })

  it('appends parameters, numbers included, in the given order', () => {
    expect(buildPath('/api/v1/engines', { risk: 'warning', limit: 25, offset: 0 })).toBe(
      '/api/v1/engines?risk=warning&limit=25&offset=0',
    )
  })

  it('omits undefined and empty values so an unset filter is never sent as an empty parameter', () => {
    expect(buildPath('/api/v1/alerts', { status: undefined, severity: '', limit: 25 })).toBe('/api/v1/alerts?limit=25')
    expect(buildPath('/api/v1/alerts', { status: undefined })).toBe('/api/v1/alerts')
  })

  it('keeps a numeric zero (offset 0 is a real value)', () => {
    expect(buildPath('/x', { offset: 0 })).toBe('/x?offset=0')
  })

  it('encodes unsafe characters', () => {
    expect(buildPath('/x', { search: 'a b&c=d' })).toBe('/x?search=a+b%26c%3Dd')
  })
})
