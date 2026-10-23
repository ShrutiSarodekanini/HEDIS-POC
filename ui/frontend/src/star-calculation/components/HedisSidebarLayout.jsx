import React from "react";
import { useNavigate } from "react-router-dom";

export default function HedisSidebarLayout({ title, subtitle, items, activeKey, onSelect, children }) {
  const navigate = useNavigate();

  return (
    <div className="hedis-shell">
      <aside className="hedis-sidebar" aria-label="HEDIS Sidebar">
        <div className="hedis-brand-row">
          <span className="brand-mark">●</span>
          <div className="hedis-brand">HEDIS Ops Console</div>
        </div>
        <nav className="hedis-nav">
          {items.map((item) => {
            const isActive = item.key === activeKey;
            return (
              <button
                key={item.key}
                type="button"
                className={`hedis-nav-item${isActive ? " is-active" : ""}`}
                onClick={() => {
                  onSelect(item.key);
                  if (item.to) {
                    navigate(item.to);
                  }
                }}
                aria-current={isActive ? "page" : undefined}
              >
                {item.icon ? <span className="hedis-nav-icon">{item.icon}</span> : null}
                <span>{item.label}</span>
                {item.badge ? <small>{item.badge}</small> : null}
              </button>
            );
          })}
        </nav>
      </aside>

      <div className="hedis-main">
        <div className="hedis-top-utility">
          <span className="hedis-pill">POC · SYNTHETIC DATA ONLY</span>
          <span className="hedis-avatar">SS</span>
        </div>
        <header className="hedis-main-header">
          <h1>{title}</h1>
          {subtitle ? <p>{subtitle}</p> : null}
        </header>
        <div className="hedis-main-content">{children}</div>
      </div>
    </div>
  );
}
