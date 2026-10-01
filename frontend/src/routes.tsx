import type { ReactElement } from "react"
import { Navigate, Route } from "react-router-dom"
import manifest from "../../data/spa_routes.json"
import PageMeta from "./components/PageMeta"
import PagePassword from "./components/PagePassword"
import CastleSplat from "./pages/CastleSplat"
import Civics from "./pages/Civics"
import Crafting from "./pages/Crafting"
import Home from "./pages/Home"
import Item from "./pages/Item"
import Items from "./pages/Items"
import Jobs from "./pages/Jobs"
import MapPage from "./pages/Map"
import Mods from "./pages/Mods"
import Recipe from "./pages/Recipe"
import Recipes from "./pages/Recipes"
import Replay from "./pages/Replay"
import Species from "./pages/Species"
import Trade from "./pages/Trade"
import Uses from "./pages/Uses"
import UsesArbitrage from "./pages/UsesArbitrage"
import UsesBuySell from "./pages/UsesBuySell"
import UsesDemand from "./pages/UsesDemand"
import UsesFood from "./pages/UsesFood"
import UsesPrice from "./pages/UsesPrice"
import UsesResolve from "./pages/UsesResolve"
import UsesShopCheck from "./pages/UsesShopCheck"
import Wiki from "./pages/Wiki"

// The route table is data, in ../../data/spa_routes.json, because the Python
// service reads the same file to build robots.txt, the sitemap, the 404 rule
// for unknown paths, and each response's X-Robots-Tag. When the two lived
// apart, a route added here was a page the crawl policy did not know about.
//
// This module turns that data into react-router elements. The manifest names a
// component; PAGES is the only place a name becomes code, so a route with no
// component fails the build rather than rendering blank.

const PAGES: Record<string, () => ReactElement> = {
  Home,
  Mods,
  Wiki,
  Jobs,
  Trade,
  Crafting,
  Items,
  Item,
  Recipes,
  Recipe,
  Civics,
  Uses,
  UsesDemand,
  UsesFood,
  UsesBuySell,
  UsesArbitrage,
  UsesPrice,
  UsesResolve,
  UsesShopCheck,
  Map: MapPage,
  Species,
  Replay,
  CastleSplat,
}

export interface RouteSpec {
  path: string
  component: string
  crawl?: string
  deepCrawl?: string
  gate?: string
  note?: string
  title?: string
  description?: string
  image?: string
}

export const routeSpecs = manifest.routes as RouteSpec[]
export const redirectSpecs = manifest.redirects as { from: string; to: string }[]

function elementFor(spec: RouteSpec): ReactElement {
  const Page = PAGES[spec.component]
  if (!Page) throw new Error(`spa_routes.json names an unknown component: ${spec.component}`)
  // A password gate is declared in the manifest rather than hand-wired here, so
  // the crawl policy and the gate cannot disagree about which pages are public.
  return (
    <>
      <PageMeta spec={spec} />
      {spec.gate === "password" ? (
        <PagePassword>
          <Page />
        </PagePassword>
      ) : (
        <Page />
      )}
    </>
  )
}

// The rendered shape is spelled out so a consumer (and the parity test) can
// read `path` and `element` off each entry without casting through unknown.
type RouteElement = ReactElement<{ path: string; element: ReactElement }>

export function spaRoutes(): RouteElement[] {
  const pages = routeSpecs.map((spec) => (
    <Route key={spec.path} path={spec.path} element={elementFor(spec)} />
  ))
  // Retired paths (eco-app#90, #93). The service answers a hard load with a
  // 301 so a crawler collapses the duplicate; this keeps an in-app click on a
  // stale link working without a round trip.
  const moved = redirectSpecs.map(({ from, to }) => (
    <Route key={from} path={from} element={<Navigate to={to} replace />} />
  ))
  return [...pages, ...moved]
}
