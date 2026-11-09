import { describe, expect, it } from 'vitest'
import { describeResolution, formatRul, pluralize } from './format'

describe('pluralize', () => {
  it('uses the singular for exactly one and the plural otherwise', () => {
    expect(pluralize(1, 'engine')).toBe('engine')
    expect(pluralize(0, 'engine')).toBe('engines')
    expect(pluralize(2, 'engine')).toBe('engines')
    expect(pluralize(60, 'engine')).toBe('engines')
  })

  it('accepts an irregular plural', () => {
    expect(pluralize(1, 'index', 'indices')).toBe('index')
    expect(pluralize(3, 'index', 'indices')).toBe('indices')
  })
})

describe('formatRul', () => {
  it('shows one decimal place', () => {
    expect(formatRul(45.5)).toBe('45.5')
    expect(formatRul(12)).toBe('12.0')
    expect(formatRul(0)).toBe('0.0')
    expect(formatRul(0.04)).toBe('0.0')
  })
})

describe('describeResolution', () => {
  it('words the two documented reasons', () => {
    expect(describeResolution('recovered')).toBe('Recovered')
    expect(describeResolution('escalated')).toBe('Escalated (replaced by a new alert)')
  })

  it('has nothing to say when there is no reason', () => {
    expect(describeResolution(null)).toBeNull()
  })

  it('shows an unknown reason as received instead of guessing', () => {
    expect(describeResolution('manual')).toBe('manual')
  })
})
