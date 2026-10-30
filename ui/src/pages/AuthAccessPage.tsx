import { useMemo, useState } from "react"

import type { AuthSession } from "../models/auth"
import { SignInPage } from "./SignInPage"
import { SignUpPage } from "./SignUpPage"
import { signIn, signUp } from "../services/auth.service"
import { saveAuthSession } from "../services/authSession"
import { toRequestErrorMessage } from "../services/http"

interface AuthAccessPageProps {
  appName: string
  authAvailable: boolean
  onSignedIn: (session: AuthSession) => void
}

type AuthMode = "signin" | "signup"

export function AuthAccessPage({ appName, authAvailable, onSignedIn }: AuthAccessPageProps) {
  const [mode, setMode] = useState<AuthMode>("signin")
  const [email, setEmail] = useState<string>("")
  const [password, setPassword] = useState<string>("")
  const [confirmPassword, setConfirmPassword] = useState<string>("")
  const [loading, setLoading] = useState<boolean>(false)
  const [errorMessage, setErrorMessage] = useState<string>("")
  const [infoMessage, setInfoMessage] = useState<string>("")

  const title = useMemo(
    () => (mode === "signin" ? "Sign in" : "Create account"),
    [mode],
  )

  return (
    <section className="auth-shell" id="view-auth">
      <article className="card auth-card">
        <div className="card-head row-split">
          <h2>{title}</h2>
          <span className="chip info">{appName}</span>
        </div>

        <p className="note">
          {mode === "signin"
            ? "Sign in with your approved account to access ingestion, quality, evidence, and star workflows."
            : "Register with a lowercase email. An administrator must approve access before first sign-in."}
        </p>

        {!authAvailable ? (
          <p className="card-message error">
            Authentication endpoints are not available on this backend profile. Start the integration backend to sign in.
          </p>
        ) : null}

        <div className="grid two auth-mode-toggle" role="tablist" aria-label="Authentication mode">
          <button
            type="button"
            className={`btn ${mode === "signin" ? "primary" : "outline"}`}
            onClick={() => {
              setMode("signin")
              setErrorMessage("")
              setInfoMessage("")
            }}
          >
            Sign in
          </button>
          <button
            type="button"
            className={`btn ${mode === "signup" ? "primary" : "outline"}`}
            onClick={() => {
              setMode("signup")
              setErrorMessage("")
              setInfoMessage("")
            }}
          >
            Sign up
          </button>
        </div>

        {mode === "signin" ? (
          <SignInPage
            email={email}
            password={password}
            loading={loading}
            onEmailChange={setEmail}
            onPasswordChange={setPassword}
            onSubmit={() => {
              void (async () => {
                setLoading(true)
                setErrorMessage("")
                setInfoMessage("")

                try {
                  const session = await signIn(email.trim(), password)
                  saveAuthSession(session)
                  onSignedIn(session)
                } catch (error) {
                  setErrorMessage(toRequestErrorMessage(error))
                } finally {
                  setLoading(false)
                }
              })()
            }}
          />
        ) : (
          <SignUpPage
            email={email}
            password={password}
            confirmPassword={confirmPassword}
            loading={loading}
            onEmailChange={setEmail}
            onPasswordChange={setPassword}
            onConfirmPasswordChange={setConfirmPassword}
            onSubmit={() => {
              void (async () => {
                setLoading(true)
                setErrorMessage("")
                setInfoMessage("")

                try {
                  if (password !== confirmPassword) {
                    setErrorMessage("Passwords do not match.")
                    return
                  }

                  const result = await signUp(email.trim(), password)
                  setInfoMessage(result.message)
                  setMode("signin")
                  setPassword("")
                  setConfirmPassword("")
                } catch (error) {
                  setErrorMessage(toRequestErrorMessage(error))
                } finally {
                  setLoading(false)
                }
              })()
            }}
          />
        )}

        {infoMessage ? <p className="card-message">{infoMessage}</p> : null}
        {errorMessage ? <p className="card-message error">{errorMessage}</p> : null}
      </article>
    </section>
  )
}
