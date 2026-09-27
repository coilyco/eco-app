import type { ReactNode } from "react"

// A chart's axis labels, as page text around its SVG instead of text inside
// it. SVG text is sized in viewBox units and shrinks with the drawing, so a
// label written at 11 units rendered at 6px on a phone. Out here it is ordinary
// 16px text a screen reader reads in order. docs/frontend/kit-checks.md.
export default function ChartFrame({
  above = [],
  start,
  end,
  children,
}: {
  above?: ReactNode[]
  start?: ReactNode
  end?: ReactNode
  children: ReactNode
}) {
  return (
    <figure className="chart-frame">
      {above.length > 0 && (
        <p className="chart-axis chart-axis--above">
          {above.map((label, i) => (
            <span key={i}>{label}</span>
          ))}
        </p>
      )}
      {children}
      {(start !== undefined || end !== undefined) && (
        <p className="chart-axis">
          <span>{start}</span>
          <span>{end}</span>
        </p>
      )}
    </figure>
  )
}
