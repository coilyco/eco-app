import { useEffect } from "react"
import { useLocation } from "react-router-dom"
import { applyPageMeta, canonicalFor, type MetaSpec } from "../lib/pageMeta"

// Renders nothing. Applies the route's title and description on every
// navigation, query string included, since a query costs the page its canonical.
export default function PageMeta({ spec }: { spec: MetaSpec }) {
  const { pathname, search } = useLocation()
  useEffect(() => {
    applyPageMeta({ title: spec.title, description: spec.description, canonical: canonicalFor(spec, pathname, search) })
  }, [spec, pathname, search])
  return null
}
