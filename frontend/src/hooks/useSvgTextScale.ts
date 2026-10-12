import { useLayoutEffect, useRef } from "react"

// For labels that must sit inside the drawing (graph nodes, marker names):
// publishes how many viewBox units one screen pixel spans as --svg-px on the
// SVG, so CSS can size text at calc(16 * var(--svg-px) * 1px) and hold the
// 16px floor at any width.
export function useSvgTextScale<T extends SVGSVGElement>() {
  const ref = useRef<T>(null)
  useLayoutEffect(() => {
    const svg = ref.current
    if (!svg || typeof ResizeObserver === "undefined") return
    const update = () => {
      const box = svg.viewBox.baseVal
      // The drawing fills the content box, so a bordered or padded svg (the price
      // chart) would otherwise read wider than it draws and land under the floor.
      const style = getComputedStyle(svg)
      const inset = ["borderLeftWidth", "borderRightWidth", "paddingLeft", "paddingRight"].reduce(
        (sum, side) => sum + (parseFloat(style[side as "paddingLeft"]) || 0),
        0,
      )
      const width = svg.getBoundingClientRect().width - inset
      if (box && box.width && width) svg.style.setProperty("--svg-px", String(box.width / width))
    }
    update()
    const observer = new ResizeObserver(update)
    observer.observe(svg)
    return () => observer.disconnect()
  }, [])
  return ref
}
