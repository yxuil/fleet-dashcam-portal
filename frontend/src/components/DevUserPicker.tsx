/**
 * Bottom-right corner dev tool: pick the synthetic user that the API
 * wrapper attaches `X-Dev-*` headers for.  Renders nothing in prod.
 *
 * Selecting a user persists the choice and reloads the page so React
 * Query's cache resets — simpler than threading invalidate-all wiring
 * through every hook in T10.
 */

import { useState } from "react";

import { DEV_USERS, getActiveDevUser, setDevUser } from "@/lib/auth";
import { IS_DEV } from "@/lib/env";
import { cn } from "@/lib/utils";

import { Dropdown, DropdownItem, DropdownLabel, DropdownSeparator } from "./ui/Dropdown";

export function DevUserPicker() {
  if (!IS_DEV) return null;
  return <DevUserPickerInner />;
}

function DevUserPickerInner() {
  // Re-render the trigger label after `setDevUser` resolves; the reload
  // below makes this mostly cosmetic but it keeps the click visibly
  // acknowledged even before the reload finishes.
  const [activeId, setActiveId] = useState<string | null>(
    () => getActiveDevUser()?.user_id ?? null,
  );
  const active = DEV_USERS.find((u) => u.user_id === activeId) ?? DEV_USERS[0];

  return (
    <div
      className="fixed bottom-4 right-4 z-50"
      data-testid="dev-user-picker"
    >
      <Dropdown
        align="right"
        trigger={({ open }) => (
          <button
            type="button"
            className={cn(
              "rounded-full border border-border bg-background px-3 py-1.5 text-xs font-medium shadow-md hover:bg-accent",
              open && "bg-accent",
            )}
            data-testid="dev-user-picker-trigger"
          >
            <span className="text-muted-foreground">Dev user:</span>{" "}
            <span>{active?.label ?? "(none)"}</span>
            <span aria-hidden className="ml-1">
              ▾
            </span>
          </button>
        )}
      >
        <DropdownLabel>Switch dev user</DropdownLabel>
        <DropdownSeparator />
        {DEV_USERS.map((u) => (
          <DropdownItem
            key={u.id}
            onSelect={() => {
              setDevUser(u.user_id, u.tenant_id);
              setActiveId(u.user_id);
              // Reloading is the simplest way to flush every cached
              // query against the new principal — fine for a dev tool.
              window.location.reload();
            }}
          >
            <div className="flex items-center justify-between gap-3">
              <span>{u.label}</span>
              {u.user_id === activeId ? (
                <span aria-hidden className="text-xs text-muted-foreground">
                  ✓
                </span>
              ) : null}
            </div>
          </DropdownItem>
        ))}
      </Dropdown>
    </div>
  );
}
