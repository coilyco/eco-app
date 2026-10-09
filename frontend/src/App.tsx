import * as Sentry from "@sentry/react"
import { BrowserRouter, Routes } from "react-router-dom"
import PageCrash from "./components/PageCrash"
import { spaRoutes } from "./routes"

// Every route comes from data/spa_routes.json via ./routes — see the note
// there for why the table is data and not a literal in this file.
export default function App() {
  return (
    <BrowserRouter>
      <Sentry.ErrorBoundary fallback={<PageCrash />}>
        <Routes>{spaRoutes()}</Routes>
      </Sentry.ErrorBoundary>
    </BrowserRouter>
  )
}
