import { describe, expect, it } from 'vitest'
import { ApiError, NetworkError, ResponseValidationError } from '../api/errors'
import { metricEntries } from '../lib/format'
import { pollInterval, shouldRetry } from './policy'

describe('pollInterval', () => {
  it('polls only while auto-refresh is on', () => {
    expect(pollInterval(true, 5000)).toBe(5000)
    expect(pollInterval(false, 5000)).toBe(false)
  })
})

describe('shouldRetry', () => {
  it('retries transient failures once', () => {
    expect(shouldRetry(0, new NetworkError())).toBe(true)
    expect(shouldRetry(0, new ApiError(503, 'x', 'x'))).toBe(true)
    expect(shouldRetry(1, new NetworkError())).toBe(false)
  })
  it('never retries 4xx or contract violations', () => {
    expect(shouldRetry(0, new ApiError(404, 'x', 'x'))).toBe(false)
    expect(shouldRetry(0, new ResponseValidationError('/p', []))).toBe(false)
  })
})

describe('metricEntries', () => {
  it('keeps finite numbers and flattens one level, ignoring the rest', () => {
    expect(metricEntries({ a: 1, b: { c: 2, d: 'x' }, e: 'y', f: NaN, g: [1], h: null })).toEqual([
      { key: 'a', value: 1 },
      { key: 'b.c', value: 2 },
    ])
  })
})
