import { render, screen, within } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { buildChartGeometry } from '../lib/chart'
import type { ChartPrediction } from '../lib/chart'
import { RulChart } from './RulChart'

const point = (id: number, cycle: number, rul: number, version = 'v1', risk: ChartPrediction['risk_category'] = 'normal'): ChartPrediction => ({
  id,
  cycle,
  predicted_rul: rul,
  risk_category: risk,
  model_version: version,
})

const chartSvg = () => screen.getByRole('img') as unknown as SVGSVGElement
/** The hover titles of the plotted markers: exactly one per plotted prediction. */
const markerTitles = () => [...chartSvg().querySelectorAll('title')].map((t) => t.textContent ?? '').filter((text) => text.startsWith('Cycle '))

const history200 = Array.from({ length: 200 }, (_, i) => point(i + 1, 163 + i, 120 - i * 0.6, 'v1', i < 100 ? 'normal' : i < 170 ? 'warning' : 'critical'))

describe('RulChart: no data', () => {
  it('says so instead of drawing an empty chart', () => {
    render(<RulChart predictions={[]} />)
    expect(screen.getByText('There are no predictions to plot.')).toBeInTheDocument()
    expect(screen.queryByRole('img')).not.toBeInTheDocument()
  })
})

describe('RulChart: accessibility', () => {
  it('is an image with a title and a plain-language description as its accessible name', () => {
    render(<RulChart predictions={[point(1, 10, 80), point(2, 11, 70), point(3, 12, 60)]} />)
    expect(chartSvg()).toHaveAccessibleName(/Predicted RUL by cycle/)
    expect(chartSvg()).toHaveAccessibleName(/3 predictions from cycle 10 to cycle 12/)
    expect(chartSvg()).toHaveAccessibleName(/Latest point: cycle 12, predicted RUL 60\.0 cycles/)
  })

  it('labels both axes with their units and shows tick values', () => {
    render(<RulChart predictions={[point(1, 10, 80), point(2, 20, 20)]} />)
    expect(screen.getByText('Cycle (operating cycle number)')).toBeInTheDocument()
    expect(screen.getByText('Predicted RUL (cycles)')).toBeInTheDocument()
    const geometry = buildChartGeometry([point(1, 10, 80), point(2, 20, 20)])
    for (const tick of geometry?.yTicks ?? []) {
      expect(within(chartSvg() as unknown as HTMLElement).getAllByText(String(tick.value)).length).toBeGreaterThan(0)
    }
  })

  it('keeps the legend icons out of the accessibility tree and names every shape in text', () => {
    render(<RulChart predictions={[point(1, 1, 50, 'alpha'), point(2, 2, 45, 'beta')]} />)
    const icons = document.querySelectorAll('svg[aria-hidden="true"]')
    expect(icons.length).toBeGreaterThanOrEqual(4) // two version icons and three risk icons
    expect(screen.getByText('(circle markers, solid line)')).toBeInTheDocument()
    expect(screen.getByText('(square markers, dashed line)')).toBeInTheDocument()
  })

  it('does not rely on colour alone: risk categories are listed in words and each point has hover text', () => {
    render(<RulChart predictions={[point(1, 1, 90, 'v1', 'normal'), point(2, 2, 45, 'v1', 'warning'), point(3, 3, 10, 'v1', 'critical')]} />)
    const legend = screen.getByRole('heading', { name: 'Risk category (marker colour)' }).parentElement as HTMLElement
    for (const label of ['Normal', 'Warning', 'Critical']) expect(within(legend).getByText(label)).toBeInTheDocument()
    expect(markerTitles()).toEqual([
      'Cycle 1: predicted RUL 90.0 cycles, Normal risk, model v1',
      'Cycle 2: predicted RUL 45.0 cycles, Warning risk, model v1',
      'Cycle 3: predicted RUL 10.0 cycles, Critical risk, model v1',
    ])
  })

  it('has a keyboard-focusable, named scroll region around the chart', () => {
    render(<RulChart predictions={[point(1, 1, 50)]} />)
    const region = screen.getByRole('region', { name: /Predicted RUL chart/ })
    expect(region).toHaveAttribute('tabindex', '0')
    expect(region).toContainElement(chartSvg() as unknown as HTMLElement)
  })
})

describe('RulChart: responsive structure', () => {
  it('scales through a viewBox with no fixed width or height, but never below a readable width', () => {
    render(<RulChart predictions={history200} />)
    const svg = chartSvg()
    expect(svg).toHaveAttribute('viewBox', '0 0 640 320')
    expect(svg).not.toHaveAttribute('width')
    expect(svg).not.toHaveAttribute('height')
    expect(svg).toHaveClass('w-full', 'h-auto', 'min-w-[34rem]')
    expect(screen.getByRole('region', { name: /scrolls sideways/ })).toHaveClass('overflow-x-auto')
  })

  it('puts the legend in normal flow below the chart so it wraps on narrow screens', () => {
    render(<RulChart predictions={[point(1, 1, 50, 'a'), point(2, 2, 40, 'b')]} />)
    const legend = screen.getByRole('heading', { name: 'Model versions' }).parentElement?.parentElement as HTMLElement
    expect(legend).toHaveClass('grid')
    expect(legend.className).toMatch(/sm:grid-cols-2/)
    expect(legend.closest('svg')).toBeNull()
  })
})

describe('RulChart: what is plotted', () => {
  it('plots all 200 predictions, each exactly once', () => {
    render(<RulChart predictions={history200} />)
    expect(markerTitles()).toHaveLength(200)
    expect(new Set(markerTitles()).size).toBe(200)
  })

  it('plots one prediction as a lone marker with no connecting line', () => {
    render(<RulChart predictions={[point(1, 7, 33.3)]} />)
    expect(markerTitles()).toEqual(['Cycle 7: predicted RUL 33.3 cycles, Normal risk, model v1'])
    expect(chartSvg().querySelectorAll('path[fill="none"]')).toHaveLength(0)
    expect(chartSvg()).toHaveAccessibleName(/1 prediction at cycle 7/)
  })

  it('draws one connecting line per model version that has several points', () => {
    render(<RulChart predictions={[point(1, 1, 50, 'a'), point(2, 2, 48, 'a'), point(3, 2, 47, 'b'), point(4, 3, 46, 'b'), point(5, 4, 40, 'c')]} />)
    expect(chartSvg().querySelectorAll('path[fill="none"]')).toHaveLength(2) // 'c' has a single point: marker only
    expect(chartSvg().querySelector('path[fill="none"][stroke-dasharray]')).not.toBeNull() // 'b' is dashed
  })

  it('keeps several predictions at one cycle as separate points and says so', () => {
    render(<RulChart predictions={[point(1, 5, 50, 'old'), point(2, 5, 44, 'new'), point(3, 6, 43, 'new')]} />)
    expect(markerTitles()).toEqual([
      'Cycle 5: predicted RUL 50.0 cycles, Normal risk, model old',
      'Cycle 5: predicted RUL 44.0 cycles, Normal risk, model new',
      'Cycle 6: predicted RUL 43.0 cycles, Normal risk, model new',
    ])
    expect(screen.getByText(/1 cycle has more than one prediction \(from different model versions\)\. Every prediction is plotted\./)).toBeInTheDocument()
    expect(chartSvg()).toHaveAccessibleName(/1 cycle has more than one prediction; all are plotted/)
  })

  it('identifies model versions by marker shape: one shape per version, listed in the legend', () => {
    render(<RulChart predictions={[point(1, 1, 50, 'alpha'), point(2, 2, 49, 'alpha'), point(3, 3, 40, 'beta'), point(4, 4, 39, 'beta'), point(5, 5, 38, 'beta')]} />)
    expect(chartSvg().querySelectorAll('circle')).toHaveLength(2) // alpha
    expect(chartSvg().querySelectorAll('rect')).toHaveLength(3) // beta
    const legend = screen.getByRole('heading', { name: 'Model versions' }).parentElement as HTMLElement
    expect(within(legend).getByText('alpha')).toBeInTheDocument()
    expect(within(legend).getByText('beta')).toBeInTheDocument()
    expect(screen.getByText(/These predictions come from 2 model versions/)).toBeInTheDocument()
  })

  it('does not mention several versions when there is only one', () => {
    render(<RulChart predictions={[point(1, 1, 50), point(2, 2, 49)]} />)
    expect(screen.queryByText(/come from \d+ model versions/)).not.toBeInTheDocument()
    expect(screen.queryByText(/more than one prediction/)).not.toBeInTheDocument()
  })

  it('colours each marker by the risk category the API reported', () => {
    render(<RulChart predictions={[point(1, 1, 90, 'v', 'normal'), point(2, 2, 45, 'v', 'warning'), point(3, 3, 10, 'v', 'critical')]} />)
    const circles = [...chartSvg().querySelectorAll('circle')]
    expect(circles[0]).toHaveClass('fill-emerald-600')
    expect(circles[1]).toHaveClass('fill-amber-500')
    expect(circles[2]).toHaveClass('fill-red-600')
  })
})

describe('RulChart: honesty about what the numbers are', () => {
  it('says predicted RUL is an estimate, not actual remaining life, and that no thresholds are drawn', () => {
    render(<RulChart predictions={history200} />)
    expect(screen.getByText(/Predicted RUL is the model's estimate, not the engine's actual remaining life\./)).toBeInTheDocument()
    expect(screen.getByText(/no thresholds are drawn/)).toBeInTheDocument()
  })

  it('draws only the grid, ticks and two axes: no reference or threshold lines', () => {
    render(<RulChart predictions={history200} />)
    const geometry = buildChartGeometry(history200)
    const expectedLines = (geometry?.yTicks.length ?? 0) + (geometry?.xTicks.length ?? 0) + 2
    expect(chartSvg().querySelectorAll('line')).toHaveLength(expectedLines)
  })

  it('never calls predictions actual or true values in any visible text', () => {
    render(<RulChart predictions={history200} />)
    expect(document.body.textContent).not.toMatch(/ground[- ]truth|true RUL|actual RUL/i)
  })
})
