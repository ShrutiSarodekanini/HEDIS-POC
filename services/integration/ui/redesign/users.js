// User Administration: administrator approval and role management for local accounts.
"use strict";
(() => {
  const statusClass = status => status === "approved" ? "status status-ok"
    : status === "pending" ? "status status-warn" : "status status-bad";

  const displayLabel = value => value.charAt(0).toUpperCase() + value.slice(1);

  const roleButton = user => {
    if (user.email === state.auth.email) return $("span", { class: "muted small" }, "Current account");
    const role = user.role === "admin" ? "User" : "Admin";
    return $("button", { type: "button", class: "btn btn-secondary calc-small", onclick: () => changeRole(user) }, role);
  };

  const accessButtons = user => {
    if (user.email === state.auth.email) return $("span", { class: "muted small" }, "Current account");
    if (user.access_status === "approved") {
      return $("button", { type: "button", class: "btn btn-reject calc-small", onclick: () => changeAccess(user, "rejected") },
        "Reject");
    }
    return $("div", { class: "btn-row" },
      $("button", { type: "button", class: "btn btn-primary calc-small", onclick: () => changeAccess(user, "approved") },
        "Approve"),
      user.access_status === "pending" ? $("button", {
        type: "button", class: "btn btn-reject calc-small", onclick: () => changeAccess(user, "rejected"),
      }, "Reject") : null);
  };

  async function changeAccess(user, accessStatus) {
    try {
      await api(`/api/admin/users/${user.user_id}/access`, {
        method: "PUT", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ access_status: accessStatus }),
      });
      toast(`User ${accessStatus}.`);
      await render();
    } catch (err) {
      toast(err.message);
    }
  }

  async function changeRole(user) {
    const role = user.role === "admin" ? "user" : "admin";
    try {
      await api(`/api/admin/users/${user.user_id}/role`, {
        method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ role }),
      });
      toast(`User role changed to ${role}.`);
      await render();
    } catch (err) {
      toast(err.message);
    }
  }

  async function render() {
    const root = byId("users-root");
    const response = await api("/api/admin/users");
    const users = response.users || [];
    const pending = users.filter(user => user.access_status === "pending").length;
    const rows = users.map(user => $("tr", {},
      $("td", {}, user.email),
      $("td", {}, $("span", { class: statusClass(user.access_status) }, displayLabel(user.access_status))),
      $("td", {}, displayLabel(user.role)),
      $("td", {}, when(user.created_at)),
      $("td", {}, accessButtons(user)),
      $("td", {}, roleButton(user)),
    ));
    if (!rows.length) rows.push($("tr", {}, $("td", { colspan: "6", class: "empty" }, "No registered users.")));
    const body = $("tbody", {}, ...rows);
    root.replaceChildren(
      $("article", { class: "card" },
        $("div", { class: "card-head" }, $("div", {}, $("h2", {}, "Registered users"),
          $("p", { class: "note" }, "Approve access before a new account can sign in.")),
          $("span", { class: pending ? "status status-warn" : "status status-ok" },
            `${pending} pending`)),
        $("div", { class: "table-wrap" }, $("table", { class: "table compact" },
          $("thead", {}, $("tr", {}, $("th", {}, "Email"), $("th", {}, "Access"), $("th", {}, "Role"),
            $("th", {}, "Registered"), $("th", {}, "Access Action"), $("th", {}, "Role Action"))),
          body))));
  }

  TABS.users = { render };
})();
