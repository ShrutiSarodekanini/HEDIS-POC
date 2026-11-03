import { StrictMode } from "react"
import { createRoot } from "react-dom/client"
import App from "./App"
import { installClientErrorReporter } from "./services/clientErrors"
import "./styles/app.css"

installClientErrorReporter()

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <App />
  </StrictMode>,
)
