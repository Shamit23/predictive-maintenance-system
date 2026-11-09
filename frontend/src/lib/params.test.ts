import { describe, expect, it } from 'vitest'
import { offsetForPage, pageParamForOffset, parseDigits, parseEnum, parseId, parsePage, withParams } from './params'

describe('parsePage', () => {
  it('accepts plain positive integers', () => {
    expect(parsePage('1')).toBe(1)
    expect(parsePage('25')).toBe(25)
    expect(parsePage('999999')).toBe(999999)
  })

  it.each([null, '', '0', '-1', 'abc', '1.5', '1e3', ' 2', '2 ', '+3', '007', '1000000'])(
    'normalises %j to page 1',
    (raw) => {
      expect(parsePage(raw)).toBe(1)
    },
  )
})

describe('offsetForPage', () => {
  it('converts a 1-based page to an offset', () => {
    expect(offsetForPage(1, 25)).toBe(0)
    expect(offsetForPage(3, 25)).toBe(50)
  })
})

describe('parseEnum', () => {
  const allowed = ['normal', 'warning', 'critical'] as const
  it('returns a value that is exactly allowed', () => {
    expect(parseEnum('warning', allowed)).toBe('warning')
  })
  it.each([null, '', 'Warning', 'WARNING', 'warn', ' warning', 'none', '__proto__'])('ignores %j', (raw) => {
    expect(parseEnum(raw, allowed)).toBeUndefined()
  })
})

describe('parseDigits', () => {
  it('accepts digits up to the limit', () => {
    expect(parseDigits('24', 20)).toBe('24')
    expect(parseDigits('0123', 20)).toBe('0123')
    expect(parseDigits('1'.repeat(20), 20)).toBe('1'.repeat(20))
  })
  it.each([null, '', 'abc', '12a', '1 2', '-1', '1.5', '%', '1'.repeat(21)])('ignores %j', (raw) => {
    expect(parseDigits(raw, 20)).toBeUndefined()
  })
})

describe('parseId', () => {
  it('accepts positive integers', () => {
    expect(parseId('1')).toBe(1)
    expect(parseId('91100')).toBe(91100)
  })
  it.each([null, '', '0', '-5', 'abc', '1.5', '01', '1234567890'])('ignores %j', (raw) => {
    expect(parseId(raw)).toBeUndefined()
  })
})

describe('withParams', () => {
  it('sets defined values and removes undefined ones without touching the rest', () => {
    const next = withParams(new URLSearchParams('status=open&page=3&severity=critical'), { status: 'resolved', severity: undefined })
    expect(next.toString()).toBe('status=resolved&page=3')
  })

  it('does not modify the original', () => {
    const original = new URLSearchParams('page=2')
    withParams(original, { page: undefined })
    expect(original.toString()).toBe('page=2')
  })

  it('adds a parameter that was absent', () => {
    expect(withParams(new URLSearchParams(), { q: '24' }).toString()).toBe('q=24')
  })
})

describe('pageParamForOffset', () => {
  it('leaves page 1 as no parameter and numbers the rest', () => {
    expect(pageParamForOffset(0, 25)).toBeUndefined()
    expect(pageParamForOffset(25, 25)).toBe('2')
    expect(pageParamForOffset(50, 25)).toBe('3')
  })
})
