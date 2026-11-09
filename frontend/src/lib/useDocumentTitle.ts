import { useEffect } from 'react'

/** Gives the page its own document title (screen readers and the tab strip name the page from it). */
export function useDocumentTitle(title: string): void {
  useEffect(() => {
    document.title = `${title} | Predictive Maintenance`
  }, [title])
}
