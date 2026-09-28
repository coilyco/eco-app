import React from "react"
import ReactDOM from "react-dom/client"
import App from "./App"
import { initCrashReporting, onUncaughtError } from "./lib/crash"
import "./eco-theme.css"
import "./index.css"

// Baked in at build time; unset leaves Sentry off.
initCrashReporting(import.meta.env.VITE_SENTRY_DSN)

ReactDOM.createRoot(document.getElementById("root")!, { onUncaughtError }).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
)
