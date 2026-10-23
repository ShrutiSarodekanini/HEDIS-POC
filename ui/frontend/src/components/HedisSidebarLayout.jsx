import React from "react";

export default function HedisSidebarLayout({ title, items, activeKey, onSelect, children }) {
  return (
    <div className="hedis-shell">
      <aside className="hedis-sidebar" aria-label="HEDIS Sidebar">
        <div className="hedis-brand">HEDIS</div>
        <nav className="hedis-nav">
          {items.map((item) => {
            const isActive = item.key === activeKey;
            return (
              <button
                key={item.key}
                type="button"
                className={`hedis-nav-item${isActive ? " is-active" : ""}`}
                onClick={() => onSelect(item.key)}
                aria-current={isActive ? "page" : undefined}
              >
                <span>{item.label}</span>
                {item.badge ? <small>{item.badge}</small> : null}
              </button>
            );
          })}
        </nav>
      </aside>

      <div className="hedis-main">
        <header className="hedis-main-header">
          <h1>{title}</h1>
        </header>
        <div className="hedis-main-content">{children}</div>
      </div>
    </div>
  );
}
