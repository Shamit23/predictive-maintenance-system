import { describe, expect, it } from 'vitest'
import { ApiError, NetworkError, ResponseValidationError } from '../api/errors'
import { groupMetrics, metricEntries } from '../lib/format'
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

describe('groupMetrics', () => {
  it('splits each key at its FIRST dot into group and in-group name', () => {
    expect(groupMetrics([{ key: 'validation_all_cycles.xgboost.mae', value: 9.5 }])).toEqual([
      { group: 'validation_all_cycles', entries: [{ name: 'xgboost.mae', value: 9.5 }] },
    ])
  })

  it('collects several entries per group and several groups, preserving input order', () => {
    const grouped = groupMetrics([
      { key: 'b.x.n', value: 1 },
      { key: 'a.y.n', value: 2 },
      { key: 'b.x.mae', value: 3 },
      { key: 'a.z', value: 4 },
    ])
    expect(grouped.map((g) => g.group)).toEqual(['b', 'a']) // first-appearance order, not alphabetical
    expect(grouped[0]?.entries).toEqual([
      { name: 'x.n', value: 1 },
      { name: 'x.mae', value: 3 },
    ])
    expect(grouped[1]?.entries).toEqual([
      { name: 'y.n', value: 2 },
      { name: 'z', value: 4 },
    ])
  })

  it('places dotless keys in a General group instead of dropping them', () => {
    expect(groupMetrics([{ key: 'rmse', value: 1 }, { key: 'g.m', value: 2 }, { key: 'mae', value: 3 }])).toEqual([
      {
        group: 'General',
        entries: [
          { name: 'rmse', value: 1 },
          { name: 'mae', value: 3 },
        ],
      },
      { group: 'g', entries: [{ name: 'm', value: 2 }] },
    ])
  })

  it('returns an empty array for empty input', () => {
    expect(groupMetrics([])).toEqual([])
  })

  it('does not split on a leading dot: the metric goes to General under its full key', () => {
    expect(groupMetrics([{ key: '.lead', value: 1 }])).toEqual([
      { group: 'General', entries: [{ name: '.lead', value: 1 }] },
    ])
  })

  it('does not split on a trailing dot: no blank label, the full key is kept', () => {
    expect(groupMetrics([{ key: 'trail.', value: 1 }])).toEqual([
      { group: 'General', entries: [{ name: 'trail.', value: 1 }] },
    ])
  })

  it('still splits at the first dot when later dots are awkward', () => {
    expect(groupMetrics([{ key: 'a..b', value: 1 }, { key: 'a.b.', value: 2 }])).toEqual([
      {
        group: 'a',
        entries: [
          { name: '.b', value: 1 },
          { name: 'b.', value: 2 },
        ],
      },
    ])
  })

  it('keeps colliding metrics instead of merging or dropping them', () => {
    // A dotted top-level key and a nested object produce the same flattened key; both values must survive.
    const colliding = metricEntries({ 'a.b': 1, a: { b: 2 } })
    expect(colliding).toEqual([
      { key: 'a.b', value: 1 },
      { key: 'a.b', value: 2 },
    ])
    expect(groupMetrics(colliding)).toEqual([
      {
        group: 'a',
        entries: [
          { name: 'b', value: 1 },
          { name: 'b', value: 2 },
        ],
      },
    ])
  })

  it('keeps a dotless key and a "General.x" key side by side in General', () => {
    expect(groupMetrics([{ key: 'x', value: 1 }, { key: 'General.x', value: 2 }])).toEqual([
      {
        group: 'General',
        entries: [
          { name: 'x', value: 1 },
          { name: 'x', value: 2 },
        ],
      },
    ])
  })

  it('never loses an entry across all the awkward shapes at once', () => {
    const input = [
      { key: 'rmse', value: 1 },
      { key: '.lead', value: 2 },
      { key: 'trail.', value: 3 },
      { key: 'a.b', value: 4 },
      { key: 'a.b', value: 5 },
      { key: 'General.x', value: 6 },
    ]
    const total = groupMetrics(input).reduce((sum, g) => sum + g.entries.length, 0)
    expect(total).toBe(input.length)
    expect(groupMetrics(input).flatMap((g) => g.entries.map((e) => e.value)).sort()).toEqual([1, 2, 3, 4, 5, 6])
  })

  it('neither drops nor duplicates entries', () => {
    const input = metricEntries({ top: 1, a: { x: { n: 1, m: 2 }, y: 3 }, b: { z: { w: 4 } }, c: { c: 0 } })
    const grouped = groupMetrics(input)
    const total = grouped.reduce((sum, g) => sum + g.entries.length, 0)
    expect(total).toBe(input.length)
    const rebuilt = grouped.flatMap((g) => g.entries.map((e) => (g.group === 'General' ? e.name : `${g.group}.${e.name}`)))
    expect(rebuilt.sort()).toEqual(input.map((e) => e.key).sort())
  })
})
