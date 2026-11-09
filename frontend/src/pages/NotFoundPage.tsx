import { Link } from 'react-router'
import { useDocumentTitle } from '../lib/useDocumentTitle'

export default function NotFoundPage() {
  useDocumentTitle('Page not found')
  return (
    <div>
      <h1 className="text-2xl font-bold">Page not found</h1>
      <p className="mt-2">
        <Link to="/" className="text-blue-700 underline">
          Back to System status
        </Link>
      </p>
    </div>
  )
}
