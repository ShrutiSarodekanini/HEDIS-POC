import { navigationItems, type NavigationItem } from "../constants/navigation"

export const isAdmin = (role: string | null | undefined): boolean => role === "admin"

// Views missing from the navigation list are treated as admin only.
export const canOpenView = (view: string, role: string | null | undefined): boolean =>
  isAdmin(role) || navigationItems.some((item) => item.id === view && !item.adminOnly)

export const visibleNavigationItems = (role: string | null | undefined): NavigationItem[] =>
  navigationItems.filter((item) => canOpenView(item.id, role))
