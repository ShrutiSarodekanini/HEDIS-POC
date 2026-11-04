import { useCallback, useEffect, useMemo, useState } from "react"

import type { AdminUserRecord, AuthRole } from "../models/auth"
import { listAdminUsers, updateUserAccess, updateUserRole } from "../services/auth.service"
import { toRequestErrorMessage } from "../services/http"
import { formatDateTime } from "../utils/format"

interface AdministratorPageProps {
  actorEmail: string
  actorRole: AuthRole
}

const statusClass = (status: AdminUserRecord["access_status"]): string => {
  if (status === "approved") {
    return "status status-ok"
  }
  if (status === "pending") {
    return "status status-warn"
  }
  return "status status-bad"
}

const labelCase = (value: string): string => `${value.charAt(0).toUpperCase()}${value.slice(1)}`

export function AdministratorPage({ actorEmail, actorRole }: AdministratorPageProps) {
  const [users, setUsers] = useState<AdminUserRecord[]>([])
  const [loading, setLoading] = useState<boolean>(true)
  const [actionBusyId, setActionBusyId] = useState<number | null>(null)
  const [errorMessage, setErrorMessage] = useState<string>("")

  const pendingCount = useMemo(
    () => users.filter((user) => user.access_status === "pending").length,
    [users],
  )

  const approvedCount = useMemo(
    () => users.filter((user) => user.access_status === "approved").length,
    [users],
  )

  const rejectedCount = useMemo(
    () => users.filter((user) => user.access_status === "rejected").length,
    [users],
  )

  const refresh = useCallback(async (): Promise<void> => {
    setLoading(true)
    setErrorMessage("")
    try {
      setUsers(await listAdminUsers())
    } catch (error) {
      setErrorMessage(toRequestErrorMessage(error))
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    if (actorRole !== "admin") {
      setLoading(false)
      return
    }
    void refresh()
  }, [actorRole, refresh])

  if (actorRole !== "admin") {
    return (
      <section id="view-admin" className="view">
        <article className="card danger-box">
          <p><strong>Administrator access required.</strong> This page is visible only for users with role "admin".</p>
        </article>
      </section>
    )
  }

  return (
    <section id="view-admin" className="view admin-view">
      <div className="page-head">
        <div>
          <h1>Administrator</h1>
          <p>Approve pending accounts and manage user roles for the integration backend.</p>
        </div>
      </div>

      <article className="card admin-summary-card">
        <div className="card-head row-split">
          <h2>Access summary</h2>
          <span className="status status-ok">Signed in as {actorEmail}</span>
        </div>

        <div className="kpi-grid four admin-kpis">
          <article className="kpi-card">
            <span>Total users</span>
            <strong>{users.length}</strong>
          </article>
          <article className="kpi-card">
            <span>Pending approval</span>
            <strong>{pendingCount}</strong>
          </article>
          <article className="kpi-card">
            <span>Approved</span>
            <strong>{approvedCount}</strong>
          </article>
          <article className="kpi-card">
            <span>Rejected</span>
            <strong>{rejectedCount}</strong>
          </article>
        </div>

        <p className="note">New sign-ups remain pending until approved by an administrator.</p>
      </article>

      <article className="card admin-users-card">
        <div className="card-head row-split">
          <h2>Registered users</h2>
          <span className={pendingCount > 0 ? "status status-warn" : "status status-ok"}>
            {pendingCount} pending
          </span>
        </div>

        <p className="note">Manage account access first, then assign role for approved users.</p>

        {errorMessage ? <p className="card-message error">{errorMessage}</p> : null}

        <div className="table-wrap admin-table-wrap">
          <table>
            <thead>
              <tr>
                <th>Email</th>
                <th>Access</th>
                <th>Role</th>
                <th>Registered</th>
                <th>Access action</th>
                <th>Role action</th>
              </tr>
            </thead>
            <tbody>
              {users.length > 0 ? users.map((user) => {
                const isActor = user.email === actorEmail
                const actionBusy = actionBusyId === user.user_id
                const nextRole: AuthRole = user.role === "admin" ? "user" : "admin"

                return (
                  <tr key={user.user_id} className={`admin-row-${user.access_status}`}>
                    <td>
                      <div className="admin-user-cell">
                        <strong>{user.email}</strong>
                        {isActor ? <span className="note">Current account</span> : null}
                      </div>
                    </td>
                    <td><span className={statusClass(user.access_status)}>{labelCase(user.access_status)}</span></td>
                    <td>
                      <span className={`chip ${user.role === "admin" ? "info" : "good"}`}>
                        {labelCase(user.role)}
                      </span>
                    </td>
                    <td>{formatDateTime(user.created_at)}</td>
                    <td>
                      {isActor ? (
                        <span className="muted">Current account</span>
                      ) : user.access_status === "approved" ? (
                        <button
                          type="button"
                          className="btn outline calc-small"
                          disabled={actionBusy}
                          onClick={() => {
                            void (async () => {
                              setActionBusyId(user.user_id)
                              setErrorMessage("")
                              try {
                                await updateUserAccess(user.user_id, "rejected")
                                await refresh()
                              } catch (error) {
                                setErrorMessage(toRequestErrorMessage(error))
                              } finally {
                                setActionBusyId(null)
                              }
                            })()
                          }}
                        >
                          Reject
                        </button>
                      ) : (
                        <div className="actions">
                          <button
                            type="button"
                            className="btn primary calc-small"
                            disabled={actionBusy}
                            onClick={() => {
                              void (async () => {
                                setActionBusyId(user.user_id)
                                setErrorMessage("")
                                try {
                                  await updateUserAccess(user.user_id, "approved")
                                  await refresh()
                                } catch (error) {
                                  setErrorMessage(toRequestErrorMessage(error))
                                } finally {
                                  setActionBusyId(null)
                                }
                              })()
                            }}
                          >
                            Approve
                          </button>
                          <button
                            type="button"
                            className="btn outline calc-small"
                            disabled={actionBusy}
                            onClick={() => {
                              void (async () => {
                                setActionBusyId(user.user_id)
                                setErrorMessage("")
                                try {
                                  await updateUserAccess(user.user_id, "rejected")
                                  await refresh()
                                } catch (error) {
                                  setErrorMessage(toRequestErrorMessage(error))
                                } finally {
                                  setActionBusyId(null)
                                }
                              })()
                            }}
                          >
                            Reject
                          </button>
                        </div>
                      )}
                    </td>
                    <td>
                      {isActor ? (
                        <span className="muted">Current account</span>
                      ) : (
                        <button
                          type="button"
                          className="btn outline calc-small"
                          disabled={actionBusy || user.access_status !== "approved"}
                          onClick={() => {
                            void (async () => {
                              setActionBusyId(user.user_id)
                              setErrorMessage("")
                              try {
                                await updateUserRole(user.user_id, nextRole)
                                await refresh()
                              } catch (error) {
                                setErrorMessage(toRequestErrorMessage(error))
                              } finally {
                                setActionBusyId(null)
                              }
                            })()
                          }}
                        >
                          Set {labelCase(nextRole)}
                        </button>
                      )}
                    </td>
                  </tr>
                )
              }) : (
                <tr>
                  <td colSpan={6} className="empty">
                    {loading ? "Loading users..." : "No registered users found."}
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </article>
    </section>
  )
}
