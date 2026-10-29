interface TopBarProps {
  appName: string
  runLabel: string
  runMeta: string
  userName: string
  userRole: string
  onLogout?: () => void
}

export function TopBar({
  appName,
  runLabel,
  runMeta,
  userName,
  userRole,
  onLogout,
}: TopBarProps) {
  const initial = userName.charAt(0).toUpperCase()

  return (
    <header className="topbar">
      <div className="brand" aria-label={appName}>
        <span className="brand-dot">H</span>
        <p>{appName}</p>
      </div>

      <div className="topbar-right">
        <div className="run-status" aria-label={runLabel}>
          <span className="run-label">{runLabel}</span>
          <span className="run-meta">{runMeta}</span>
        </div>

        <details className="profile-menu">
          <summary className="avatar" aria-label="Open user profile menu">
            {initial}
          </summary>
          <div className="profile-dropdown" role="menu" aria-label="Profile actions">
            <p className="profile-name">{userName}</p>
            <p className="profile-role">{userRole}</p>
            <button
              type="button"
              className="btn profile-logout"
              role="menuitem"
              onClick={() => {
                onLogout?.()
              }}
            >
              Logout
            </button>
          </div>
        </details>
      </div>
    </header>
  )
}
