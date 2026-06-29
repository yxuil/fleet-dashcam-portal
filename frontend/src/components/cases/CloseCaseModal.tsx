/**
 * Modal to collect a close-reason and fire `POST /cases/:id/close`.
 *
 * Tiny on purpose — one required `reason` field, a submit button, and
 * an error pane. The parent owns the mutation; this is presentation
 * only. Mirrors OpenCaseModal's hand-rolled overlay (no `<dialog>`)
 * so the tests can mount it without jsdom shenanigans.
 */

import { useEffect, useId, useState } from "react";

import { Button } from "@/components/ui/Button";
import { ApiError } from "@/lib/api";
import { cn } from "@/lib/utils";

export type CloseCaseModalProps = {
  open: boolean;
  isSubmitting: boolean;
  error: unknown;
  onSubmit: (reason: string) => void;
  onClose: () => void;
};

export function CloseCaseModal({
  open,
  isSubmitting,
  error,
  onSubmit,
  onClose,
}: CloseCaseModalProps) {
  const titleId = useId();
  const [reason, setReason] = useState("");

  // Reset on each open so a previous (failed) attempt doesn't linger.
  useEffect(() => {
    if (open) setReason("");
  }, [open]);

  useEffect(() => {
    if (!open) return;
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape" && !isSubmitting) onClose();
    }
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [open, isSubmitting, onClose]);

  if (!open) return null;

  const canSubmit = reason.trim().length > 0 && !isSubmitting;
  const errorMessage = formatError(error);

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-labelledby={titleId}
      data-testid="close-case-modal"
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4"
      onClick={(e) => {
        if (e.target === e.currentTarget && !isSubmitting) onClose();
      }}
    >
      <form
        className="w-full max-w-md space-y-4 rounded-lg border border-border bg-background p-6 shadow-lg"
        onSubmit={(e) => {
          e.preventDefault();
          if (canSubmit) onSubmit(reason.trim());
        }}
      >
        <h2 id={titleId} className="text-lg font-semibold tracking-tight">
          Close case
        </h2>

        <label className="block space-y-1">
          <span className="text-xs font-medium text-muted-foreground">
            Reason <span aria-hidden>*</span>
          </span>
          <textarea
            value={reason}
            onChange={(e) => setReason(e.target.value)}
            disabled={isSubmitting}
            rows={3}
            required
            data-testid="close-case-reason"
            className={cn(
              "w-full rounded-md border border-input bg-background px-3 py-2 text-sm",
              "focus:outline-none focus-visible:ring-2 focus-visible:ring-ring",
              "disabled:opacity-50",
            )}
          />
        </label>

        {errorMessage !== null ? (
          <p
            role="alert"
            data-testid="close-case-error"
            className="rounded-md border border-destructive/50 bg-destructive/5 p-2 text-xs text-destructive"
          >
            {errorMessage}
          </p>
        ) : null}

        <div className="flex items-center justify-end gap-2 pt-2">
          <Button
            type="button"
            variant="outline"
            size="sm"
            onClick={onClose}
            disabled={isSubmitting}
            data-testid="close-case-cancel"
          >
            Cancel
          </Button>
          <Button
            type="submit"
            size="sm"
            disabled={!canSubmit}
            data-testid="close-case-submit"
          >
            {isSubmitting ? "Closing…" : "Close case"}
          </Button>
        </div>
      </form>
    </div>
  );
}

function formatError(err: unknown): string | null {
  if (err === null || err === undefined) return null;
  if (err instanceof ApiError) {
    return `Couldn’t close case (${err.status}): ${err.detail}`;
  }
  if (err instanceof Error) return `Couldn’t close case: ${err.message}`;
  return "Couldn’t close case.";
}
