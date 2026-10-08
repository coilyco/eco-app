import hero from "../assets/terrace-farms-v1-hero.jpg"
import SplatPage, { type SplatConfig } from "./SplatPage"

// Ice Dragon's terrace farms and bridge, cycle 14. The title and caption are Kai's,
// from COI-2541, and the facts are from the capture's run.json in eco-ops. Everything
// but the numbers is SplatPage, docs/frontend/biodrive-splat.md.
export const SPLAT_URL = "https://files.coilysiren.me/eco/cycle-14/terrace-farms-v1.sog"

export const TERRACE_FARMS: SplatConfig = {
  slug: "terrace-farms",
  subject: "the terrace farms",
  title: "Terrace farms",
  caption: "Ice Dragon's terrace farms and bridge in Phantom Springs, cycle 14.",
  hero,
  heroAlt: "Ice Dragon's terrace farms and bridge in Phantom Springs, a still from the 3D capture.",
  splatUrl: SPLAT_URL,
  size: "11.5 MB",
  facts: [
    ["Frames", "315 in six passes, 22.5 minutes"],
    ["Reconstruction", "COLMAP 4.2.1, 315 of 315 registered at 0.74 px"],
    ["Training", "Brush v0.3.0, 30,000 steps at 3440 px"],
    ["Quality", "26.35 dB PSNR over 21 held-out frames"],
    ["Splats", "616,523, after trimming"],
    ["Footprint", "About 160 by 90 blocks"],
  ],
  // About 160 by 90 blocks, close to the castle's 150, so the castle's zoom range and far
  // clip fit. The start camera is where the still was taken, from the south and above,
  // which hides the floaters on the south edge that a low view from there shows.
  view: {
    name: "terrace-farms",
    target: [0, 12, 0],
    reference: [0, 60, -115],
    fov: 50,
    distanceRange: [15, 400],
    farClip: 2000,
  },
}

export default function TerraceFarmsSplat() {
  return <SplatPage config={TERRACE_FARMS} />
}
