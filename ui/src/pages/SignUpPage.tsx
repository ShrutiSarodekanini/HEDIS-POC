interface SignUpPageProps {
  email: string
  password: string
  confirmPassword: string
  loading: boolean
  onEmailChange: (value: string) => void
  onPasswordChange: (value: string) => void
  onConfirmPasswordChange: (value: string) => void
  onSubmit: () => void
}

export function SignUpPage({
  email,
  password,
  confirmPassword,
  loading,
  onEmailChange,
  onPasswordChange,
  onConfirmPasswordChange,
  onSubmit,
}: SignUpPageProps) {
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
          autoComplete="email"
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
          autoComplete="new-password"
          value={password}
          onChange={(event) => {
            onPasswordChange(event.target.value)
          }}
          required
        />
      </label>

      <label className="calc-check">
        <span>Confirm password</span>
        <input
          className="text-input"
          type="password"
          autoComplete="new-password"
          value={confirmPassword}
          onChange={(event) => {
            onConfirmPasswordChange(event.target.value)
          }}
          required
        />
      </label>

      <div className="actions">
        <button type="submit" className="btn primary" disabled={loading}>
          {loading ? "Please wait..." : "Create account"}
        </button>
      </div>
    </form>
  )
}
