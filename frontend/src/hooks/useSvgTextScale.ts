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
      const width = svg.getBoundingClientRect().width
      if (box && box.width && width) svg.style.setProperty("--svg-px", String(box.width / width))
    }
    update()
    const observer = new ResizeObserver(update)
    observer.observe(svg)
    return () => observer.disconnect()
  }, [])
  return ref
}
