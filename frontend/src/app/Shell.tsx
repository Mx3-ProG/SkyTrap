import { useEffect, useState } from "react";
import { NavLink, Outlet, useNavigate } from "react-router-dom";
import { apiRequest } from "../api/client";
import { CommandPalette } from "./CommandPalette";

type NavItem = { label: string; to?: string; disabled?: boolean };
type NavSection = { title: string; items: NavItem[] };

// Sections marked disabled are part of the target information architecture
// (agents/skills/models/missions/permissions/settings) that this session's
// vertical slice doesn't implement yet — shown so the shell reads as the real
// target shape, never as a working feature that silently does nothing.
const SECTIONS: NavSection[] = [
  { title: "", items: [{ label: "Home", to: "/" }, { label: "Projects", to: "/projects" }] },
  {
    title: "Intelligence",
    items: [
      { label: "Agents", disabled: true },
      { label: "Skills", disabled: true },
      { label: "Models", disabled: true },
    ],
  },
  {
    title: "Automation",
    items: [
      { label: "Missions", disabled: true },
      { label: "Activity", disabled: true },
    ],
  },
  {
    title: "System",
    items: [
      { label: "Permissions", disabled: true },
      { label: "Logs", disabled: true },
      { label: "Settings", disabled: true },
    ],
  },
];

export function Shell({ onLoggedOut }: { onLoggedOut: () => void }) {
  const [collapsed, setCollapsed] = useState(false);
  const [paletteOpen, setPaletteOpen] = useState(false);
  const navigate = useNavigate();

  useEffect(() => {
    function onKeyDown(event: KeyboardEvent) {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
        setPaletteOpen((open) => !open);
      }
    }
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, []);

  return (
    <div className={`shell ${collapsed ? "shell-collapsed" : ""}`}>
      <aside className="sidebar">
        <div className="sidebar-header">
          <span className="sidebar-logo">SKYTRAP</span>
          <button
            className="icon-button"
            type="button"
            onClick={() => setCollapsed((c) => !c)}
            aria-label="Toggle sidebar"
          >
            ⌷
          </button>
        </div>
        <nav className="sidebar-nav">
          {SECTIONS.map((section) => (
            <div className="sidebar-section" key={section.title || "main"}>
              {section.title && <div className="sidebar-section-title">{section.title}</div>}
              {section.items.map((item) =>
                item.to ? (
                  <NavLink
                    key={item.label}
                    to={item.to}
                    end={item.to === "/"}
                    className={({ isActive }) => `sidebar-item ${isActive ? "active" : ""}`}
                  >
                    {item.label}
                  </NavLink>
                ) : (
                  <span className="sidebar-item disabled" key={item.label} title="Not available yet">
                    {item.label}
                    <span className="badge">soon</span>
                  </span>
                )
              )}
            </div>
          ))}
        </nav>
        <button
          className="sidebar-item logout"
          type="button"
          onClick={async () => {
            await apiRequest("/auth/logout", { method: "POST" });
            onLoggedOut();
          }}
        >
          Déconnexion
        </button>
      </aside>

      <div className="shell-main">
        <header className="topbar">
          <button className="palette-trigger" type="button" onClick={() => setPaletteOpen(true)}>
            <span>Search or ask SkyTrap…</span>
            <kbd>⌘K</kbd>
          </button>
        </header>
        <main className="shell-content">
          <Outlet />
        </main>
      </div>

      {paletteOpen && (
        <CommandPalette
          onClose={() => setPaletteOpen(false)}
          onNavigate={(to) => {
            setPaletteOpen(false);
            navigate(to);
          }}
        />
      )}
    </div>
  );
}
