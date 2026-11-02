interface SignInPageProps {
  email: string
  password: string
  loading: boolean
  onEmailChange: (value: string) => void
  onPasswordChange: (value: string) => void
  onSubmit: () => void
}

export function SignInPage({
  email,
  password,
  loading,
  onEmailChange,
  onPasswordChange,
  onSubmit,
}: SignInPageProps) {
  return (
    <form
      className="auth-form"
      onSubmit={(event) => {
        event.preventDefault()
        onSubmit()
      }}
    >
      <label className="calc-check">
        <span>Email (lowercase)</span>
        <input
          className="text-input"
          type="email"
          autoComplete="username"
          value={email}
          onChange={(event) => {
            onEmailChange(event.target.value)
          }}
          required
        />
      </label>

      <label className="calc-check">
        <span>Password</span>
        <input
          className="text-input"
          type="password"
          autoComplete="current-password"
          value={password}
          onChange={(event) => {
            onPasswordChange(event.target.value)
          }}
          required
        />
      </label>

      <div className="actions">
        <button type="submit" className="btn primary" disabled={loading}>
          {loading ? "Please wait..." : "Sign in"}
        </button>
      </div>
    </form>
  )
}
