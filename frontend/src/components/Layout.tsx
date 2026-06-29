/**
 * Application shell: sidebar + topbar wrapping an `<Outlet />` for
 * route content.  Stays free of data-fetching beyond `useMe()` so the
 * shell renders even when the backend is unreachable.
 */

import { useEffect } from "react";
import { NavLink, Outlet } from "react-router-dom";

import { useMe } from "@/hooks/useMe";
import { ApiError, UNAUTHORIZED_EVENT } from "@/lib/api";
import { getTenantName } from "@/lib/auth";
import { cn } from "@/lib/utils";

import { Dropdown, DropdownItem, DropdownLabel, DropdownSeparator } from "./ui/Dropdown";

type NavItem = { to: string; label: string };

const NAV_ITEMS: readonly NavItem[] = [
  { to: "/search", label: "Search" },
  { to: "/trucks", label: "Trucks" },
  { to: "/cases", label: "Cases" },
];

export function Layout() {
  const me = useMe();

  // T10 stops short of a real re-auth UX; for now we just surface the
  // unauthorized event in the console so devs notice when their picker
  // selection drifts out of sync with the backend.
  useEffect(() => {
    function onUnauthorized() {
      // eslint-disable-next-line no-console
      console.warn(
        "[auth] /me (or another endpoint) returned 401 — pick a dev user from the bottom-right corner.",
      );
    }
    window.addEventListener(UNAUTHORIZED_EVENT, onUnauthorized);
    return () => window.removeEventListener(UNAUTHORIZED_EVENT, onUnauthorized);
  }, []);

  return (
    <div className="flex min-h-screen flex-col bg-background text-foreground">
      <TopBar
        userName={me.data?.name}
        tenantName={getTenantName(me.data?.tenant_id) || undefined}
        loading={me.isLoading}
        errored={me.isError ? me.error : null}
      />
      <div className="flex flex-1">
        <Sidebar />
        <main className="flex-1 p-6">
          <Outlet />
        </main>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Top bar
// ---------------------------------------------------------------------------

type TopBarProps = {
  userName: string | undefined;
  tenantName: string | undefined;
  loading: boolean;
  errored: unknown;
};

function TopBar({ userName, tenantName, loading, errored }: TopBarProps) {
  let userLabel = "Loading…";
  if (!loading) {
    if (errored) {
      const status =
        errored instanceof ApiError ? ` (${errored.status})` : "";
      userLabel = `Auth error${status}`;
    } else if (userName) {
      userLabel = userName;
    } else {
      userLabel = "Anonymous";
    }
  }

  return (
    <header className="flex h-14 items-center justify-between border-b border-border bg-background px-6">
      <div className="flex items-center gap-3">
        <span className="text-lg font-semibold tracking-tight">Dashcam Portal</span>
      </div>
      <div className="flex items-center gap-4">
        <span
          className="text-sm text-muted-foreground"
          data-testid="tenant-name"
        >
          {tenantName ?? (loading ? "…" : "—")}
        </span>
        <Dropdown
          trigger={({ open }) => (
            <button
              type="button"
              className={cn(
                "flex items-center gap-2 rounded-md px-3 py-1.5 text-sm font-medium hover:bg-accent",
                open && "bg-accent",
              )}
              data-testid="user-menu-trigger"
            >
              <span data-testid="user-name">{userLabel}</span>
              <span aria-hidden className="text-xs">
                ▾
              </span>
            </button>
          )}
        >
          <DropdownLabel>Signed in as</DropdownLabel>
          <div className="px-3 py-1 text-xs text-muted-foreground">
            {userName ?? "—"}
          </div>
          <DropdownSeparator />
          <DropdownItem
            onSelect={() => {
              // Placeholder until a real sign-out flow exists (T??).
              // eslint-disable-next-line no-alert
              alert("Sign out is not wired up in dev — use the bottom-right picker.");
            }}
          >
            Sign out
          </DropdownItem>
        </Dropdown>
      </div>
    </header>
  );
}

// ---------------------------------------------------------------------------
// Sidebar
// ---------------------------------------------------------------------------

function Sidebar() {
  return (
    <nav
      aria-label="Primary"
      className="w-56 border-r border-border bg-muted/40 p-4"
    >
      <ul className="flex flex-col gap-1">
        {NAV_ITEMS.map((item) => (
          <li key={item.to}>
            <NavLink
              to={item.to}
              className={({ isActive }) =>
                cn(
                  "block rounded-md px-3 py-2 text-sm transition-colors",
                  isActive
                    ? "bg-primary text-primary-foreground"
                    : "text-foreground hover:bg-accent hover:text-accent-foreground",
                )
              }
            >
              {item.label}
            </NavLink>
          </li>
        ))}
      </ul>
    </nav>
  );
}
