import type { ReactNode } from 'react'

/**
 * A horizontally scrollable table container. The scroll area is keyboard-focusable (WCAG: scrollable regions must be
 * reachable without a mouse) and named, so narrow screens scroll the table instead of the whole page.
 */
export function TableWrap({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div
      role="region"
      aria-label={label}
      tabIndex={0}
      className="overflow-x-auto rounded-lg border border-slate-200 bg-white shadow-sm focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-blue-600"
    >
      <table className="w-full min-w-max border-collapse text-left text-sm">{children}</table>
    </div>
  )
}

export function Th({ children }: { children: ReactNode }) {
  return (
    <th scope="col" className="border-b border-slate-200 bg-slate-50 px-3 py-2 font-semibold whitespace-nowrap text-slate-700">
      {children}
    </th>
  )
}

export function Td({ children }: { children: ReactNode }) {
  return <td className="border-b border-slate-100 px-3 py-2 align-top">{children}</td>
}
