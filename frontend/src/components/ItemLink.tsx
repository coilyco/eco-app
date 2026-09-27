import type { ReactNode } from "react"
import { Link } from "react-router-dom"
import { itemPageHref } from "../lib/itemPage"

// Canonical route for a concrete Eco item id. Keep the id (rather than its
// display label) in the query string: labels are lossy and may contain spaces
// or punctuation. The page key drops the `Item` suffix (eco-app#8383).
export function itemHref(item: string): string {
  return itemPageHref(item)
}

interface ItemLinkProps {
  item: string | null | undefined
  children: ReactNode
  className?: string
  "data-testid"?: string
}

// A real anchor gives item names normal keyboard, screen-reader, and browser
// link behaviour. Unknown/empty ids deliberately render plain content: callers
// sometimes have a display label or a recipe tag, neither of which identifies
// one item pivot safely.
export default function ItemLink({ item, children, className, "data-testid": testId }: ItemLinkProps) {
  if (!item?.trim()) return <>{children}</>
  return (
    <Link className={className} to={itemHref(item)} data-testid={testId}>
      {children}
    </Link>
  )
}
