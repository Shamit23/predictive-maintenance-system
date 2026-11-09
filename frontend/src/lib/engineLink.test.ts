import { describe, expect, it } from 'vitest'
import { readEngineLinkState } from './engineLink'

describe('readEngineLinkState (router state is untrusted input)', () => {
  it('accepts exactly the expected shape from either list page', () => {
    expect(readEngineLinkState({ from: 'engines', search: '?risk=warning&page=2' })).toEqual({ from: 'engines', search: '?risk=warning&page=2' })
    expect(readEngineLinkState({ from: 'alerts', search: '' })).toEqual({ from: 'alerts', search: '' })
  })

  it('drops extra properties instead of passing them on', () => {
    expect(readEngineLinkState({ from: 'engines', search: '?a=1', extra: 'x', __proto__: { polluted: true } })).toEqual({
      from: 'engines',
      search: '?a=1',
    })
  })

  it.each([
    ['null', null],
    ['undefined', undefined],
    ['a string', 'engines'],
    ['a number', 5],
    ['an array', ['engines', '?x']],
    ['an unknown origin', { from: 'evil', search: '?x=1' }],
    ['a missing origin', { search: '?x=1' }],
    ['a missing search', { from: 'engines' }],
    ['a non-string search', { from: 'engines', search: 5 }],
    ['a search that is not a query string', { from: 'engines', search: 'javascript:alert(1)' }],
    ['a search without a leading question mark', { from: 'alerts', search: 'status=all' }],
    ['an oversized search', { from: 'alerts', search: `?q=${'9'.repeat(600)}` }],
  ])('ignores %s', (_label, state) => {
    expect(readEngineLinkState(state)).toBeUndefined()
  })
})
