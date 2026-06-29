/**
 * Tiny formatters shared across the Cases pages.
 *
 * Kept in one place so the list, detail, and modal all render
 * timestamps and statuses the same way.
 */

import type { CaseStatus } from "@/lib/types";

/** Format an ISO timestamp as `12 Jun 14:32` in the user's local zone. */
export function formatDateTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return new Intl.DateTimeFormat(undefined, {
    day: "2-digit",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).format(d);
}

/** Format an ISO timestamp as `12 Jun 2026` (no time). */
export function formatDate(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return new Intl.DateTimeFormat(undefined, {
    day: "2-digit",
    month: "short",
    year: "numeric",
  }).format(d);
}

/** Human label for a `CaseStatus` enum value. */
export const STATUS_LABEL: Record<CaseStatus, string> = {
  open: "Open",
  under_review: "Under review",
  approved: "Approved",
  closed: "Closed",
};

/** Tailwind classes for the status badge background + text. */
export const STATUS_BADGE_CLASS: Record<CaseStatus, string> = {
  open: "bg-blue-100 text-blue-800",
  under_review: "bg-amber-100 text-amber-900",
  approved: "bg-emerald-100 text-emerald-900",
  closed: "bg-slate-200 text-slate-700",
};
