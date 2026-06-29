/**
 * Tiny dropdown menu component — hand-written instead of `shadcn add
 * dropdown-menu` to keep T10 dependency-free (no Radix).  Closes on:
 *   - click outside,
 *   - Escape,
 *   - clicking any `<DropdownItem>` inside.
 *
 * No keyboard arrow-navigation. Good enough for the user menu and the
 * dev-user picker; T11+ can replace this with shadcn's Radix wrapper if
 * a richer menu is ever needed.
 */

import { useEffect, useRef, useState } from "react";
import type { ReactNode } from "react";

import { cn } from "@/lib/utils";

export type DropdownProps = {
  /** Element rendered as the menu trigger. */
  trigger: (args: { open: boolean }) => ReactNode;
  /** Children rendered inside the popup panel — typically `DropdownItem`s. */
  children: ReactNode;
  /** Alignment of the popup panel relative to the trigger. */
  align?: "left" | "right";
  className?: string;
};

export function Dropdown({
  trigger,
  children,
  align = "right",
  className,
}: DropdownProps) {
  const [open, setOpen] = useState(false);
  const wrapperRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (!open) return;
    function onDocPointer(e: PointerEvent) {
      if (!wrapperRef.current) return;
      if (!wrapperRef.current.contains(e.target as Node)) {
        setOpen(false);
      }
    }
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") setOpen(false);
    }
    document.addEventListener("pointerdown", onDocPointer);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("pointerdown", onDocPointer);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  return (
    <div ref={wrapperRef} className={cn("relative inline-block", className)}>
      <div onClick={() => setOpen((o) => !o)}>{trigger({ open })}</div>
      {open ? (
        <div
          role="menu"
          className={cn(
            "absolute z-40 mt-2 min-w-[10rem] rounded-md border border-border bg-background p-1 shadow-md",
            align === "right" ? "right-0" : "left-0",
          )}
          onClick={() => setOpen(false)}
        >
          {children}
        </div>
      ) : null}
    </div>
  );
}

export type DropdownItemProps = {
  onSelect?: () => void;
  children: ReactNode;
  disabled?: boolean;
  className?: string;
};

export function DropdownItem({
  onSelect,
  children,
  disabled,
  className,
}: DropdownItemProps) {
  return (
    <button
      type="button"
      role="menuitem"
      disabled={disabled}
      onClick={(e) => {
        e.stopPropagation();
        if (!disabled) onSelect?.();
      }}
      className={cn(
        "block w-full rounded-sm px-3 py-1.5 text-left text-sm hover:bg-accent hover:text-accent-foreground disabled:pointer-events-none disabled:opacity-50",
        className,
      )}
    >
      {children}
    </button>
  );
}

export function DropdownLabel({ children }: { children: ReactNode }) {
  return (
    <div className="px-3 py-1.5 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
      {children}
    </div>
  );
}

export function DropdownSeparator() {
  return <div className="my-1 h-px bg-border" />;
}
