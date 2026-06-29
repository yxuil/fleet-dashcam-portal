/**
 * "Open case" modal: collects requester info + incident time, then asks
 * the parent to fire `useCreateCase` so the case + attachment land in
 * one round-trip.
 *
 * Hand-rolled overlay (not a `<dialog>` element) so the parent owns the
 * `open` state and the unit tests can render the modal directly without
 * messing with `HTMLDialogElement.showModal`, which jsdom doesn't ship.
 *
 * UX notes:
 *   - `incident_at` defaults to the source event's `occurred_at`,
 *     rendered as a `datetime-local` so the user can adjust without
 *     opening a separate picker.
 *   - `requester_name` is the only required field — empty submissions
 *     are blocked client-side so the user gets fast feedback.
 *   - Loading & error states are inline. The modal stays open on error
 *     so the user can fix and retry without losing what they typed.
 */

import { useEffect, useId, useState } from "react";

import { Button } from "@/components/ui/Button";
import { ApiError } from "@/lib/api";
import { cn } from "@/lib/utils";

export type OpenCaseFormValues = {
  incidentAt: string;
  requesterName: string;
  requesterOrg: string;
  externalRef: string;
};

export type OpenCaseModalProps = {
  open: boolean;
  /** ISO timestamp of the event the user is opening a case for. */
  defaultIncidentAt: string;
  isSubmitting: boolean;
  error: unknown;
  onSubmit: (values: OpenCaseFormValues) => void;
  onClose: () => void;
};

export function OpenCaseModal({
  open,
  defaultIncidentAt,
  isSubmitting,
  error,
  onSubmit,
  onClose,
}: OpenCaseModalProps) {
  const titleId = useId();
  const [values, setValues] = useState<OpenCaseFormValues>(() => ({
    incidentAt: toDatetimeLocal(defaultIncidentAt),
    requesterName: "",
    requesterOrg: "",
    externalRef: "",
  }));

  // Reset the form whenever the modal is (re)opened with a different event.
  useEffect(() => {
    if (open) {
      setValues({
        incidentAt: toDatetimeLocal(defaultIncidentAt),
        requesterName: "",
        requesterOrg: "",
        externalRef: "",
      });
    }
  }, [open, defaultIncidentAt]);

  // Escape closes — same convention as `<Dropdown>` and the user menu.
  useEffect(() => {
    if (!open) return;
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape" && !isSubmitting) onClose();
    }
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [open, isSubmitting, onClose]);

  if (!open) return null;

  const canSubmit = values.requesterName.trim().length > 0 && !isSubmitting;
  const errorMessage = formatError(error);

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-labelledby={titleId}
      data-testid="open-case-modal"
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4"
      onClick={(e) => {
        if (e.target === e.currentTarget && !isSubmitting) onClose();
      }}
    >
      <form
        className="w-full max-w-md space-y-4 rounded-lg border border-border bg-background p-6 shadow-lg"
        onSubmit={(e) => {
          e.preventDefault();
          if (canSubmit) onSubmit(values);
        }}
      >
        <h2 id={titleId} className="text-lg font-semibold tracking-tight">
          Open case
        </h2>

        <Field
          label="Incident at"
          htmlFor="open-case-incident-at"
        >
          <input
            id="open-case-incident-at"
            type="datetime-local"
            value={values.incidentAt}
            onChange={(e) =>
              setValues((v) => ({ ...v, incidentAt: e.target.value }))
            }
            disabled={isSubmitting}
            data-testid="open-case-incident-at"
            className={inputCn}
          />
        </Field>

        <Field
          label="Requester name"
          htmlFor="open-case-requester-name"
          required
        >
          <input
            id="open-case-requester-name"
            type="text"
            required
            value={values.requesterName}
            onChange={(e) =>
              setValues((v) => ({ ...v, requesterName: e.target.value }))
            }
            disabled={isSubmitting}
            data-testid="open-case-requester-name"
            className={inputCn}
          />
        </Field>

        <Field
          label="Requester org"
          htmlFor="open-case-requester-org"
        >
          <input
            id="open-case-requester-org"
            type="text"
            value={values.requesterOrg}
            onChange={(e) =>
              setValues((v) => ({ ...v, requesterOrg: e.target.value }))
            }
            disabled={isSubmitting}
            data-testid="open-case-requester-org"
            className={inputCn}
          />
        </Field>

        <Field
          label="External ref"
          htmlFor="open-case-external-ref"
        >
          <input
            id="open-case-external-ref"
            type="text"
            value={values.externalRef}
            onChange={(e) =>
              setValues((v) => ({ ...v, externalRef: e.target.value }))
            }
            disabled={isSubmitting}
            data-testid="open-case-external-ref"
            className={inputCn}
          />
        </Field>

        {errorMessage !== null ? (
          <p
            role="alert"
            data-testid="open-case-error"
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
            data-testid="open-case-cancel"
          >
            Cancel
          </Button>
          <Button
            type="submit"
            size="sm"
            disabled={!canSubmit}
            data-testid="open-case-submit"
          >
            {isSubmitting ? "Creating…" : "Create case"}
          </Button>
        </div>
      </form>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Bits
// ---------------------------------------------------------------------------

const inputCn = cn(
  "h-9 w-full rounded-md border border-input bg-background px-3 text-sm",
  "focus:outline-none focus-visible:ring-2 focus-visible:ring-ring",
  "disabled:opacity-50",
);

type FieldProps = {
  label: string;
  htmlFor: string;
  required?: boolean;
  children: React.ReactNode;
};

function Field({ label, htmlFor, required, children }: FieldProps) {
  return (
    <div className="space-y-1">
      <label
        htmlFor={htmlFor}
        className="block text-xs font-medium text-muted-foreground"
      >
        {label}
        {required ? <span aria-hidden> *</span> : null}
      </label>
      {children}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/**
 * Convert an ISO timestamp into the `YYYY-MM-DDTHH:MM` shape that
 * `<input type="datetime-local">` expects. Returns "" if the input is
 * unparseable so the field renders empty instead of "Invalid Date".
 */
function toDatetimeLocal(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  const pad = (n: number) => String(n).padStart(2, "0");
  const y = d.getFullYear();
  const mo = pad(d.getMonth() + 1);
  const da = pad(d.getDate());
  const h = pad(d.getHours());
  const mi = pad(d.getMinutes());
  return `${y}-${mo}-${da}T${h}:${mi}`;
}

function formatError(err: unknown): string | null {
  if (err === null || err === undefined) return null;
  if (err instanceof ApiError) {
    return `Couldn’t create case (${err.status}): ${err.detail}`;
  }
  if (err instanceof Error) return `Couldn’t create case: ${err.message}`;
  return "Couldn’t create case.";
}
