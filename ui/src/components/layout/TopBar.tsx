import { roleLabel } from "../../utils/access"

interface TopBarProps {
  appName: string
  runLabel: string
  runMeta: string
  userName: string
  userRole: string
  onLogout?: () => void
  menuOpen: boolean
  menuId: string
  onToggleMenu: () => void
}

export function TopBar({
  appName,
  runLabel,
  runMeta,
  userName,
  userRole,
  onLogout,
  menuOpen,
  menuId,
  onToggleMenu,
}: TopBarProps) {
  const initial = userName.charAt(0).toUpperCase()

  return (
    <header className="topbar">
      <div className="topbar-left">
        <button
          type="button"
          className="menu-toggle"
          aria-label={menuOpen ? "Hide menu" : "Show menu"}
          aria-expanded={menuOpen}
          aria-controls={menuId}
          onClick={onToggleMenu}
        >
          <svg width="18" height="18" viewBox="0 0 18 18" aria-hidden="true" focusable="false">
            <path d="M2 4h14M2 9h14M2 14h14" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
          </svg>
        </button>
        <div className="brand" aria-label={appName}>
          <span className="brand-dot">H</span>
          <p>{appName}</p>
        </div>
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
            <p className="profile-role">{roleLabel(userRole)}</p>
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
