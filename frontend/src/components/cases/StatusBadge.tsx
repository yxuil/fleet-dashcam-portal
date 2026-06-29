/**
 * Small inline status pill for case rows / headers.
 *
 * Pure presentation — no data fetching. The color mapping lives in
 * `format.ts` so the list and detail share one source of truth.
 */

import type { CaseStatus } from "@/lib/types";
import { cn } from "@/lib/utils";

import { STATUS_BADGE_CLASS, STATUS_LABEL } from "./format";

type Props = {
  status: CaseStatus;
  className?: string;
};

export function StatusBadge({ status, className }: Props) {
  return (
    <span
      data-testid={`case-status-${status}`}
      className={cn(
        "inline-flex items-center rounded-full px-2 py-0.5 text-[11px] font-medium uppercase tracking-wide",
        STATUS_BADGE_CLASS[status],
        className,
      )}
    >
      {STATUS_LABEL[status]}
    </span>
  );
}
