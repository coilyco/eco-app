import { useEffect, useRef, useState } from "react"
import { Link } from "react-router-dom"
import Layout from "../components/Layout"
import Loading from "../components/Loading"
import hero from "../assets/castle-v2-hero.jpg"
import type { SplatViewer } from "../lib/splatViewer"

// A Gaussian splat of the cycle 14 castle, and two flythroughs of it. The splat and
// the videos are too big to commit (the repo caps files at 2 MB), so they load from
// the files host. docs/frontend/castle-splat.md.
const FILES = "https://files.coilysiren.me/eco/cycle-14"
export const SPLAT_URL = `${FILES}/castle-v2.sog`

const FLYTHROUGHS = [
  { n: 1, seconds: 55, megabytes: 27 },
  { n: 2, seconds: 30, megabytes: 25 },
]

type Phase = "unsupported" | "loading" | "ready" | "failed"

const FACTS: Array<[string, string]> = [
  ["Frames", "300, four rings and a top-down grid"],
  ["Reconstruction", "COLMAP 4.2.1, 300 of 300 registered at 0.76 px"],
  ["Training", "Brush v0.3.0, 30,000 steps at 3440 px"],
  ["Quality", "28.05 dB PSNR over 20 held-out frames"],
  ["Splats", "386,757, after trimming"],
]

// One flythrough, as its poster on a play button. The video is not in the page until
// someone presses it, since each is about 25 MB, and a file that will not load says
// so instead of leaving a dead player.
type VideoPhase = "idle" | "playing" | "failed"

function Flythrough({ n, seconds, megabytes }: { n: number; seconds: number; megabytes: number }) {
  const [phase, setPhase] = useState<VideoPhase>("idle")
  // A poster that will not load leaves the labelled button on black, not a broken-image glyph.
  const [posterFailed, setPosterFailed] = useState(false)
  const name = `Flythrough ${n}`
  const base = `${FILES}/eco-cycle-14-castle-flythrough-${n}`
  return (
    <figure className="castle-video k-stack k-stack--2">
      {phase === "idle" ? (
        <button
          type="button"
          className="castle-video__frame castle-video__play"
          aria-label={`Play ${name.toLowerCase()}, ${seconds} seconds, ${megabytes} MB`}
          onClick={() => setPhase("playing")}
        >
          {posterFailed ? null : (
            <img src={`${base}-poster.jpg`} alt="" loading="lazy" decoding="async" onError={() => setPosterFailed(true)} />
          )}
          <span className="castle-video__badge" aria-hidden="true">
            Play
          </span>
        </button>
      ) : null}
      {phase === "playing" ? (
        <video
          className="castle-video__frame"
          controls
          autoPlay
          playsInline
          src={`${base}.mp4`}
          aria-label={`${name}, ${seconds} seconds`}
          onError={() => setPhase("failed")}
        />
      ) : null}
      {phase === "failed" ? (
        <div className="castle-video__frame castle-note k-stack k-stack--3" role="alert" data-testid={`castle-video-failed-${n}`}>
          <p>{name} did not load. The file may be unavailable right now.</p>
          <div className="k-btn-row">
            <button type="button" className="k-btn k-btn--quiet" onClick={() => setPhase("playing")}>
              Try again
            </button>
          </div>
        </div>
      ) : null}
      <figcaption className="k-hint">
        {name}, {seconds} seconds, {megabytes} MB
      </figcaption>
    </figure>
  )
}

// WebGL2 is the floor the engine needs. Checking the global first keeps jsdom
// (which has no canvas) from logging a not-implemented error.
function canDrawIn3d(): boolean {
  if (typeof WebGL2RenderingContext === "undefined") return false
  try {
    return Boolean(document.createElement("canvas").getContext("webgl2"))
  } catch {
    return false
  }
}

const prefersReducedMotion = () =>
  typeof window.matchMedia === "function" && window.matchMedia("(prefers-reduced-motion: reduce)").matches

export default function CastleSplat() {
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const viewerRef = useRef<SplatViewer | null>(null)
  const [phase, setPhase] = useState<Phase>(() => (canDrawIn3d() ? "loading" : "unsupported"))
  const [attempt, setAttempt] = useState(0)
  const [playing, setPlaying] = useState(() => !prefersReducedMotion())
  const playingAtStart = useRef(playing)

  useEffect(() => {
    const canvas = canvasRef.current
    if (!canvas || !canDrawIn3d()) return
    let cancelled = false
    // Deferred a tick so React's dev double-mount cancels the first start before
    // it boots a second engine on the same canvas.
    const timer = window.setTimeout(() => {
      import("../lib/splatViewer")
        .then(({ startViewer }) => startViewer(canvas, SPLAT_URL, { playing: playingAtStart.current }))
        .then((viewer) => {
          if (cancelled) {
            viewer.destroy()
            return
          }
          viewerRef.current = viewer
          setPhase("ready")
        })
        .catch(() => {
          if (!cancelled) setPhase("failed")
        })
    }, 0)
    return () => {
      cancelled = true
      window.clearTimeout(timer)
      viewerRef.current?.destroy()
      viewerRef.current = null
    }
  }, [attempt])

  useEffect(() => {
    viewerRef.current?.setPlaying(playing)
  }, [playing, phase])

  const live = phase === "ready"

  return (
    <Layout>
      <section className="k-stack k-stack--4">
        {/* dev-advocate's words, teable:coilyco/eco-app#8601. */}
        <h1 className="k-display">The castle of La Croisée des Bois</h1>
        <p className="castle-sub">
          Built by the French-speaking town of La Croisée des Bois on the Sirens Eco server in cycle 14, captured from
          300 frames as a Gaussian splat of 386,757 points.
        </p>
      </section>

      <section className="k-stack k-stack--4 castle" aria-labelledby="castle-view">
        <h2 className="k-h2" id="castle-view">
          Look around
        </h2>
        <div className="castle-stage" data-phase={phase}>
          {/* The still stands in while the splat loads and when it cannot, so the stage is never empty. */}
          <img
            className="castle-still"
            src={hero}
            width={1920}
            height={1080}
            alt="The castle of La Croisée des Bois, a still from the 3D capture."
          />
          <canvas
            ref={canvasRef}
            className={phase === "failed" || phase === "unsupported" ? "castle-canvas castle-canvas--off" : "castle-canvas"}
            // The canvas draws pixels no assistive tech can read, so it is one
            // labelled image with its controls described beside it.
            role="img"
            aria-label="3D view of the castle, a Gaussian splat. The facts below describe the capture."
            aria-describedby="castle-help"
            tabIndex={live ? 0 : -1}
            aria-hidden={!live}
          />
          {phase === "loading" ? (
            <Loading label="Loading the castle, 8.2 MB…" testid="castle-loading" />
          ) : null}
          {phase === "unsupported" ? (
            <p className="castle-note" role="status" data-testid="castle-unsupported">
              This browser cannot draw 3D with WebGL 2, so the 3D castle cannot show here. A recent desktop
              browser can.
            </p>
          ) : null}
          {phase === "failed" ? (
            <div className="castle-note k-stack k-stack--3" role="alert" data-testid="castle-failed">
              <p>The castle did not load. The file may be unavailable right now.</p>
              <div className="k-btn-row">
                <button type="button" className="k-btn k-btn--quiet" onClick={() => {
                    setPhase("loading")
                    setAttempt((n) => n + 1)
                  }}>
                  Try again
                </button>
              </div>
            </div>
          ) : null}
        </div>
        {live ? (
          <div className="k-btn-row" role="group" aria-label="View controls">
            <button type="button" className="k-btn k-btn--quiet" onClick={() => setPlaying((p) => !p)}>
              {playing ? "Pause orbit" : "Play orbit"}
            </button>
            <button type="button" className="k-btn k-btn--quiet" onClick={() => viewerRef.current?.reset()}>
              Reset view
            </button>
          </div>
        ) : null}
        <p className="k-hint" id="castle-help">
          Drag to orbit, scroll or pinch to zoom. With the view focused, the arrow keys turn it and plus and minus
          zoom.
        </p>
      </section>

      <section className="k-stack k-stack--4" aria-labelledby="castle-flythroughs">
        <h2 className="k-h2" id="castle-flythroughs">
          Flythroughs
        </h2>
        <div className="castle-videos">
          {FLYTHROUGHS.map((video) => (
            <Flythrough key={video.n} {...video} />
          ))}
        </div>
      </section>

      <section className="k-stack k-stack--3" aria-labelledby="castle-facts">
        <h2 className="k-h2" id="castle-facts">
          About the capture
        </h2>
        <dl className="k-deflist">
          {FACTS.map(([term, detail]) => (
            <div key={term}>
              <dt>{term}</dt>
              <dd>{detail}</dd>
            </div>
          ))}
        </dl>
      </section>
      <p className="k-hint">
        <Link to="/cycle-14">All cycle 14 scenes</Link>
      </p>
    </Layout>
  )
}
