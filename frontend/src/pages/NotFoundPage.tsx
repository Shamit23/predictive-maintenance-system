import { Link } from 'react-router'

export default function NotFoundPage() {
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
