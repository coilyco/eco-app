import { Link } from "react-router-dom"
import Layout from "../components/Layout"
import { splatSpecs, stillFor } from "./Cycle14Index"

// A player guide to building for a good Gaussian splat (COI-2618). The words are
// dev-advocate's v4 on that record, the facts game-dev's sheet, and the director's
// merge criteria bind them: "45 degrees" stays Kai's rule of thumb, and point 3 stays
// her observation with the camera-distance caveat, with no "sharpest" for BioDrive and no
// "only" in point 2. Change a sentence there first.
// docs/frontend/build-for-splats.md.
interface Point {
  id: string
  heading: string
  /** Kai's own words, quoted. */
  quote: string
  body: string[]
  /** The splat page whose still goes under the point. */
  splat: string
  /** One sentence on what to look at in the still, shown under it. */
  caption: string
  linkLabel: string
}

const INTRO = [
  "Your cycle 15 build could end up on this site as a 3D scene anyone can spin around. Three builds from cycle 14 already are.",
  "For each one, a camera flew rings around the build, looking down, and saved about 300 screenshots. Training turned those into hundreds of thousands of points you can orbit. The camera only learns what it saw, and some builds come out sharper than others. Here is what Kai learned capturing cycle 14, in her words, with the splat that shows each one.",
]

const POINTS: Point[] = [
  {
    id: "orbit",
    heading: "1. Put the best part where the orbit can see it",
    quote: "the most interesting parts need to be visible from a 45 degree orbit",
    body: [
      "That 45 degrees is Kai’s rule of thumb, not a measured setting. The cycle 14 cameras sat outside the build and looked down, at angles from 12 degrees down to straight overhead. No camera looks up, and none goes inside unless we add a separate interior pass. A surface no frame saw has nothing to train on, so it renders as a smear or not at all. The castle’s bell tower and keep towers stand above the roofline, so every ring sees them, and they are the sharpest part of that splat.",
      "What this costs you - anything underground, enclosed, or under an overhang, including the underside of a bridge or arch, will likely be missing. One cycle 14 base was mostly underground. The orbit could not see it, and the slower interior method was dropped for time, so there is no splat of it to show you here. Interiors are possible, but the BioDrive shop took 151 extra frames and extra fixes, so do not count on one.",
      "A rough check - from a hill or a tall build, look at yours from each corner, tilted well down. Anything you cannot see from there, the splat probably will not have either.",
    ],
    splat: "/cycle-14/castle",
    caption:
      "The castle’s bell tower and keep towers rise above the roofline, where every ring of the orbit sees them, and they are the sharpest part of the splat.",
    linkLabel: "The castle in 3D",
  },
  {
    id: "color",
    heading: "2. Give each wall a color its neighbor does not share",
    quote:
      "same color objects bleed into each other, use color difference and color shifted borders to get sharp edges on your walls",
    body: [
      "Our best explanation is that training learns an edge best where the color changes. Where two surfaces share a color, one large blob across the seam looks close enough, so the edge often never forms. On the terrace farms, tan fences along each terrace stay crisp against the green crops. Below them, green hedges on green grass blur into one green mass.",
      "You do not need a whole building that clashes. In every cycle 14 example, the work is done by the color change at the edge: trim, a fence line, a painted line. Try trimming a wall in a different block where two surfaces meet.",
    ],
    splat: "/cycle-14/terrace-farms",
    caption:
      "Tan fences along each terrace edge stay crisp against the green crops, while green hedges on green grass blur together below them.",
    linkLabel: "Terrace farms in 3D",
  },
  {
    id: "realism",
    heading: "3. Lean realistic, it seems to hold finer detail",
    quote: "more realistic builds render in higher resolution",
    body: [
      "This one is Kai’s observation, and the BioDrive station is the example. She called it the most realistic of the cycle 14 splats, the one that “wins on practical realism.” It also has the most points for its size and the finest small detail, down to lamp posts that resolve as thin poles. Its cameras sat much closer than the castle’s, though, so it is not a clean test of realism alone. Our best explanation is that textured surfaces like road, timber, and gravel keep earning finer detail during training, while a flat surface settles for a few big blobs.",
    ],
    splat: "/cycle-14/biodrive",
    caption: "The road’s lane lines, curbs, and lamp posts at the BioDrive station resolve as thin, sharp lines.",
    linkLabel: "BioDrive station in 3D",
  },
]

const CLOSING = [
  "Not every build gets captured. Kai picks which ones, and each player gets one splat at most.",
  "You do not need to change what you build. Change where you put the best of it, and what color sits next to what.",
  "When your build is ready, nominate it in #eco-cycle-15 on the Sirens Discord.",
]

// "300 frames, 386,757 points", read from the route's own description so the counts live
// in one place. A description in any other shape shows whole rather than half-parsed.
const FACTS = /captured from (\d[\d,]*) frames as a Gaussian splat of (\d[\d,]*) points/
export function captureFacts(description = ""): string {
  const m = FACTS.exec(description)
  return m ? `${m[1]} frames, ${m[2]} points` : description
}

export default function BuildForSplats({ specs = splatSpecs() }: { specs?: ReturnType<typeof splatSpecs> } = {}) {
  const byPath = new Map(specs.map((s) => [s.path, s]))
  return (
    <Layout>
      <section className="k-stack k-stack--4">
        <h1 className="k-display">Build for a splat</h1>
        {INTRO.map((line) => (
          <p className="castle-sub" key={line}>
            {line}
          </p>
        ))}
      </section>

      {POINTS.map((point) => {
        const spec = byPath.get(point.splat)
        const still = spec ? stillFor(spec) : undefined
        return (
          <section className="k-stack k-stack--3 guide-point" aria-labelledby={`guide-${point.id}`} key={point.id}>
            <h2 className="k-h2" id={`guide-${point.id}`}>
              {point.heading}
            </h2>
            <blockquote className="guide-quote">
              <p>Kai: &ldquo;{point.quote}&rdquo;</p>
            </blockquote>
            {point.body.map((paragraph) => (
              <p className="guide-body" key={paragraph}>
                {paragraph}
              </p>
            ))}
            {/* No splat in the table, no picture: the words above stand without it. The caption
                sits in the figure as text, so the still is decorative to a screen reader and the
                same sentence is not read twice. */}
            {spec && still ? (
              <figure className="guide-figure k-stack k-stack--2">
                <img src={still} width={1200} height={630} alt="" loading="lazy" />
                <figcaption className="k-stack k-stack--2">
                  <span>{point.caption}</span>
                  <Link to={spec.path}>{point.linkLabel}</Link>
                </figcaption>
              </figure>
            ) : null}
          </section>
        )
      })}

      <section className="k-stack k-stack--3" aria-labelledby="guide-splats">
        <h2 className="k-h2" id="guide-splats">
          What the cycle 14 captures show
        </h2>
        {specs.length === 0 ? (
          <p className="k-hint" data-testid="guide-splats-empty">
            No 3D scenes are published yet.
          </p>
        ) : (
          <ul className="guide-splats">
            {specs.map((s) => (
              <li key={s.path}>
                <Link to={s.path}>{s.title}</Link>
                <span className="k-hint"> {captureFacts(s.description)}</span>
              </li>
            ))}
          </ul>
        )}
        <p>
          <Link to="/cycle-14">All of cycle 14 in 3D</Link>
        </p>
      </section>

      <section className="k-stack k-stack--3" aria-labelledby="guide-cycle-15">
        <h2 className="k-h2" id="guide-cycle-15">
          Build for cycle 15
        </h2>
        {CLOSING.map((line) => (
          <p key={line}>{line}</p>
        ))}
      </section>
    </Layout>
  )
}
