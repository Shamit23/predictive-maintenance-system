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
  it('keeps finite numbers and flattens one level, ignoring the rest (existing shape stays compatible)', () => {
    expect(metricEntries({ a: 1, b: { c: 2, d: 'x' }, e: 'y', f: NaN, g: [1], h: null })).toEqual([
      { key: 'a', value: 1 },
      { key: 'b.c', value: 2 },
    ])
  })

  it('flattens nested values at several levels into dotted keys', () => {
    expect(metricEntries({ top: 1, g: { m: { mae: 9.5, deeper: { x: 2 } }, n: 3 } })).toEqual([
      { key: 'top', value: 1 },
      { key: 'g.m.mae', value: 9.5 },
      { key: 'g.m.deeper.x', value: 2 },
      { key: 'g.n', value: 3 },
    ])
  })

  it('handles the real backend structure: several groups, arrays skipped', () => {
    const real = {
      validation_all_cycles: { xgboost: { n: 3852, mae: 9.68332642731513 }, baseline_ridge: { n: 3852, mae: 16.4 } },
      classification_test_last_cycle: {
        xgb_classifier: { f1: 0.84, positives: 25, confusion_matrix: [[74, 1], [6, 19]] },
      },
    }
    const entries = metricEntries(real)
    expect(entries.map((e) => e.key)).toEqual([
      'validation_all_cycles.xgboost.n',
      'validation_all_cycles.xgboost.mae',
      'validation_all_cycles.baseline_ridge.n',
      'validation_all_cycles.baseline_ridge.mae',
      'classification_test_last_cycle.xgb_classifier.f1',
      'classification_test_last_cycle.xgb_classifier.positives',
    ])
    expect(entries.find((e) => e.key === 'validation_all_cycles.xgboost.mae')?.value).toBe(9.68332642731513)
  })

  it('never traverses arrays, even when they contain objects or numbers', () => {
    expect(metricEntries({ cm: [[1, 2], [3, 4]], list: [{ a: 1 }], ok: { cm: [1, 2], v: 5 } })).toEqual([
      { key: 'ok.v', value: 5 },
    ])
  })

  it('excludes booleans, strings, null, NaN and infinities at any depth', () => {
    expect(metricEntries({ a: true, b: { c: false, d: 'x', e: null, f: Infinity, g: -Infinity, h: NaN, i: 0 } })).toEqual([
      { key: 'b.i', value: 0 },
    ])
  })

  it('returns nothing for empty metrics or empty groups', () => {
    expect(metricEntries({})).toEqual([])
    expect(metricEntries({ a: {}, b: { c: {} } })).toEqual([])
  })
})
