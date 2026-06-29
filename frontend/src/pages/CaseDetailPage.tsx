/**
 * Case detail page — `/cases/:id`.
 *
 * Header lays out the case number + status badge + identity fields.
 * Below that, an action bar lets the user:
 *   - change status (open / under_review / approved — closed is not
 *     reachable here; use the dedicated Close button which collects a
 *     reason via the modal).
 *   - set assignee user id (free text — no /users endpoint yet; the
 *     input accepts a UUID).
 *   - set a due date.
 *   - open the AttachClipModal.
 *   - open the CloseCaseModal.
 *
 * Tabs:
 *   - Evidence: list of attached clips. Each row has an "Open" link to
 *     `/clips/:id`. Remove is intentionally NOT here — the backend
 *     doesn't have DELETE /cases/:id/clips/:clip_id (deferred).
 *   - Notes: a textarea + Add button, then a newest-first list of the
 *     case's `case.note_added` audit rows. See `useAddCaseNote`.
 *   - Activity: the full `recent_audit` stream — every mutation that
 *     happened to this case (create, attach, status update, note,
 *     close).
 *
 * The mutation hooks set the `["case", id]` cache entry on success, so
 * the header / tabs reflect the new state without a second round-trip.
 */

import { useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";

import { AttachClipModal } from "@/components/cases/AttachClipModal";
import { CloseCaseModal } from "@/components/cases/CloseCaseModal";
import { StatusBadge } from "@/components/cases/StatusBadge";
import {
  STATUS_LABEL,
  formatDate,
  formatDateTime,
} from "@/components/cases/format";
import { Button } from "@/components/ui/Button";
import { useCaseDetail } from "@/hooks/useCaseDetail";
import {
  useAddCaseNote,
  useAttachClip,
  useCloseCase,
  usePatchCase,
} from "@/hooks/useCaseMutations";
import { ApiError } from "@/lib/api";
import {
  PATCHABLE_CASE_STATUSES,
  type AttachedClip,
  type AuditEntry,
  type CaseDetail,
  type PatchableCaseStatus,
} from "@/lib/types";
import { cn } from "@/lib/utils";

type Tab = "evidence" | "notes" | "activity";

export function CaseDetailPage() {
  const { id } = useParams<{ id: string }>();
  const detail = useCaseDetail(id);

  if (detail.isLoading) {
    return <LoadingState />;
  }
  if (detail.isError) {
    const err = detail.error;
    const is404 = err instanceof ApiError && err.status === 404;
    if (is404) return <NotFoundState />;
    return <ErrorState error={err} onRetry={() => detail.refetch()} />;
  }

  const data = detail.data;
  if (!data) return <NotFoundState />;

  return <Body detail={data} />;
}

// ---------------------------------------------------------------------------
// Body — split so the mutation hooks don't run until we have the case id.
// ---------------------------------------------------------------------------

function Body({ detail }: { detail: CaseDetail }) {
  const [tab, setTab] = useState<Tab>("evidence");
  const [attachOpen, setAttachOpen] = useState(false);
  const [closeOpen, setCloseOpen] = useState(false);

  const patch = usePatchCase(detail.id);
  const attach = useAttachClip(detail.id);
  const close = useCloseCase(detail.id);
  const addNote = useAddCaseNote(detail.id);

  const isClosed = detail.status === "closed";

  return (
    <section className="space-y-6">
      <div>
        <Link
          to="/cases"
          className="text-xs text-muted-foreground underline-offset-2 hover:underline"
          data-testid="back-to-cases"
        >
          ← Back to cases
        </Link>
      </div>

      <Header detail={detail} />

      <ActionBar
        detail={detail}
        isClosed={isClosed}
        onStatusChange={(s) => patch.mutate({ status: s })}
        onAssigneeChange={(uid) =>
          patch.mutate({ assignee_user_id: uid || null })
        }
        onDueAtChange={(dt) => patch.mutate({ due_at: dt || null })}
        onAttachClick={() => setAttachOpen(true)}
        onCloseClick={() => setCloseOpen(true)}
        patchError={patch.error}
        patchPending={patch.isPending}
      />

      <Tabs current={tab} onChange={setTab} />

      {tab === "evidence" ? <EvidenceTab clips={detail.clips} /> : null}
      {tab === "notes" ? (
        <NotesTab
          audit={detail.recent_audit}
          onAdd={(text) => addNote.mutate({ text })}
          pending={addNote.isPending}
          error={addNote.error}
          disabled={isClosed}
        />
      ) : null}
      {tab === "activity" ? <ActivityTab audit={detail.recent_audit} /> : null}

      <AttachClipModal
        open={attachOpen}
        isSubmitting={attach.isPending}
        error={attach.error}
        onAttach={(clipId) => {
          attach.mutate(
            { clip_id: clipId },
            {
              onSuccess: () => setAttachOpen(false),
            },
          );
        }}
        onClose={() => setAttachOpen(false)}
      />

      <CloseCaseModal
        open={closeOpen}
        isSubmitting={close.isPending}
        error={close.error}
        onSubmit={(reason) =>
          close.mutate(
            { reason },
            {
              onSuccess: () => setCloseOpen(false),
            },
          )
        }
        onClose={() => setCloseOpen(false)}
      />
    </section>
  );
}

// ---------------------------------------------------------------------------
// Header
// ---------------------------------------------------------------------------

function Header({ detail }: { detail: CaseDetail }) {
  return (
    <header className="space-y-2" data-testid="case-detail-header">
      <div className="flex items-center gap-3">
        <h1
          className="font-mono text-2xl font-semibold tracking-tight"
          data-testid="case-detail-number"
        >
          {detail.number}
        </h1>
        <StatusBadge status={detail.status} />
      </div>
      <dl className="grid grid-cols-1 gap-x-6 gap-y-1 text-sm sm:grid-cols-2 lg:grid-cols-3">
        <Field label="External ref" testId="case-detail-external-ref">
          {detail.external_ref ?? "—"}
        </Field>
        <Field label="Requester" testId="case-detail-requester">
          {detail.requester_name ?? "—"}
          {detail.requester_org ? (
            <span className="text-muted-foreground">
              {" "}
              · {detail.requester_org}
            </span>
          ) : null}
        </Field>
        <Field label="Incident at" testId="case-detail-incident-at">
          {formatDateTime(detail.incident_at)}
        </Field>
        <Field label="Assignee" testId="case-detail-assignee">
          {detail.assignee_user_id ?? "—"}
        </Field>
        <Field label="Due" testId="case-detail-due-at">
          {formatDate(detail.due_at)}
        </Field>
        <Field label="Created" testId="case-detail-created-at">
          {formatDateTime(detail.created_at)}
        </Field>
      </dl>
    </header>
  );
}

function Field({
  label,
  testId,
  children,
}: {
  label: string;
  testId?: string;
  children: React.ReactNode;
}) {
  return (
    <div className="flex flex-col">
      <dt className="text-[11px] uppercase tracking-wide text-muted-foreground">
        {label}
      </dt>
      <dd className="text-sm" data-testid={testId}>
        {children}
      </dd>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Action bar
// ---------------------------------------------------------------------------

type ActionBarProps = {
  detail: CaseDetail;
  isClosed: boolean;
  onStatusChange: (s: PatchableCaseStatus) => void;
  onAssigneeChange: (uid: string) => void;
  onDueAtChange: (iso: string) => void;
  onAttachClick: () => void;
  onCloseClick: () => void;
  patchError: unknown;
  patchPending: boolean;
};

function ActionBar({
  detail,
  isClosed,
  onStatusChange,
  onAssigneeChange,
  onDueAtChange,
  onAttachClick,
  onCloseClick,
  patchError,
  patchPending,
}: ActionBarProps) {
  // PATCH can't move to "closed", and we don't want to surface it as an
  // option in the dropdown — the Close button captures that transition
  // with a reason instead.
  const patchableStatus: PatchableCaseStatus | "" = useMemo(() => {
    if (
      detail.status === "open" ||
      detail.status === "under_review" ||
      detail.status === "approved"
    ) {
      return detail.status;
    }
    return "";
  }, [detail.status]);

  // `<input type="date">` expects YYYY-MM-DD; convert if present.
  const dueDateValue = useMemo(() => {
    if (!detail.due_at) return "";
    const d = new Date(detail.due_at);
    if (Number.isNaN(d.getTime())) return "";
    return d.toISOString().slice(0, 10);
  }, [detail.due_at]);

  return (
    <div
      data-testid="case-detail-action-bar"
      className="flex flex-col gap-3 rounded-md border border-border bg-card p-4 lg:flex-row lg:items-end"
    >
      <label className="flex flex-col gap-1 text-xs text-muted-foreground">
        Status
        <select
          value={patchableStatus}
          disabled={isClosed || patchPending}
          onChange={(e) =>
            onStatusChange(e.target.value as PatchableCaseStatus)
          }
          data-testid="case-detail-status-select"
          className={selectCn}
        >
          {patchableStatus === "" ? (
            <option value="" disabled hidden>
              {STATUS_LABEL[detail.status]}
            </option>
          ) : null}
          {PATCHABLE_CASE_STATUSES.map((s) => (
            <option key={s} value={s}>
              {STATUS_LABEL[s]}
            </option>
          ))}
        </select>
      </label>

      <label className="flex flex-col gap-1 text-xs text-muted-foreground">
        Assignee (user id)
        <input
          type="text"
          defaultValue={detail.assignee_user_id ?? ""}
          disabled={isClosed || patchPending}
          onBlur={(e) => {
            const next = e.target.value.trim();
            if (next !== (detail.assignee_user_id ?? "")) {
              onAssigneeChange(next);
            }
          }}
          placeholder="UUID or blank"
          data-testid="case-detail-assignee-input"
          className={selectCn}
        />
      </label>

      <label className="flex flex-col gap-1 text-xs text-muted-foreground">
        Due
        <input
          type="date"
          defaultValue={dueDateValue}
          disabled={isClosed || patchPending}
          onBlur={(e) => {
            const next = e.target.value;
            const current = dueDateValue;
            if (next !== current) {
              // Convert YYYY-MM-DD to ISO at start-of-day in UTC, or
              // clear with empty string for the parent to send `null`.
              if (next) {
                const iso = new Date(`${next}T00:00:00Z`).toISOString();
                onDueAtChange(iso);
              } else {
                onDueAtChange("");
              }
            }
          }}
          data-testid="case-detail-due-input"
          className={selectCn}
        />
      </label>

      <div className="flex flex-1 items-end justify-end gap-2">
        <Button
          type="button"
          size="sm"
          variant="outline"
          onClick={onAttachClick}
          disabled={isClosed}
          data-testid="case-detail-attach"
        >
          Attach clip
        </Button>
        <Button
          type="button"
          size="sm"
          onClick={onCloseClick}
          disabled={isClosed}
          data-testid="case-detail-close"
        >
          {isClosed ? "Closed" : "Close case"}
        </Button>
      </div>

      {patchError ? (
        <p
          role="alert"
          data-testid="case-detail-patch-error"
          className="basis-full text-xs text-destructive"
        >
          {formatApiError(patchError, "Couldn’t update case")}
        </p>
      ) : null}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Tabs
// ---------------------------------------------------------------------------

function Tabs({
  current,
  onChange,
}: {
  current: Tab;
  onChange: (next: Tab) => void;
}) {
  const tabs: { id: Tab; label: string }[] = [
    { id: "evidence", label: "Evidence" },
    { id: "notes", label: "Notes" },
    { id: "activity", label: "Activity" },
  ];
  return (
    <div
      role="tablist"
      data-testid="case-detail-tabs"
      className="flex gap-1 border-b border-border"
    >
      {tabs.map((t) => (
        <button
          key={t.id}
          type="button"
          role="tab"
          aria-selected={current === t.id}
          onClick={() => onChange(t.id)}
          data-testid={`case-detail-tab-${t.id}`}
          className={cn(
            "border-b-2 px-3 py-2 text-sm font-medium transition-colors",
            current === t.id
              ? "border-primary text-foreground"
              : "border-transparent text-muted-foreground hover:text-foreground",
          )}
        >
          {t.label}
        </button>
      ))}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Evidence tab
// ---------------------------------------------------------------------------

function EvidenceTab({ clips }: { clips: readonly AttachedClip[] }) {
  if (clips.length === 0) {
    return (
      <p
        data-testid="case-detail-evidence-empty"
        className="rounded-md border border-dashed border-border p-6 text-center text-sm text-muted-foreground"
      >
        No clips attached yet.
      </p>
    );
  }
  return (
    <ul
      data-testid="case-detail-evidence"
      className="divide-y divide-border rounded-md border border-border"
    >
      {clips.map((c) => (
        <li
          key={c.clip_id}
          className="flex items-center justify-between gap-3 px-4 py-3"
          data-testid={`case-detail-clip-${c.clip_id}`}
        >
          <div className="min-w-0 flex-1">
            <div className="truncate text-sm font-medium">{c.truck_label}</div>
            <div className="text-xs text-muted-foreground">
              {formatDateTime(c.started_at)}
              {c.note ? ` · ${c.note}` : ""}
            </div>
            <div className="text-[11px] text-muted-foreground">
              Attached {formatDateTime(c.attached_at)}
            </div>
          </div>
          <Link
            to={`/clips/${c.clip_id}`}
            data-testid={`case-detail-clip-open-${c.clip_id}`}
            className="text-sm text-primary underline-offset-2 hover:underline"
          >
            Open
          </Link>
        </li>
      ))}
    </ul>
  );
}

// ---------------------------------------------------------------------------
// Notes tab
// ---------------------------------------------------------------------------

type NotesTabProps = {
  audit: readonly AuditEntry[];
  onAdd: (text: string) => void;
  pending: boolean;
  error: unknown;
  disabled: boolean;
};

function NotesTab({ audit, onAdd, pending, error, disabled }: NotesTabProps) {
  const [text, setText] = useState("");

  // Filter the audit stream to the notes-as-audit entries. The detail
  // response is already ordered newest-first, so no extra sort needed.
  const notes = useMemo(
    () => audit.filter((a) => a.action === "case.note_added"),
    [audit],
  );

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (disabled || pending) return;
    const trimmed = text.trim();
    if (!trimmed) return;
    onAdd(trimmed);
    setText("");
  }

  return (
    <div data-testid="case-detail-notes" className="space-y-4">
      <form className="space-y-2" onSubmit={handleSubmit}>
        <textarea
          value={text}
          onChange={(e) => setText(e.target.value)}
          rows={3}
          placeholder={
            disabled
              ? "Notes are read-only on closed cases."
              : "Add a note…"
          }
          disabled={disabled || pending}
          data-testid="case-detail-note-input"
          className={cn(
            "w-full rounded-md border border-input bg-background px-3 py-2 text-sm",
            "focus:outline-none focus-visible:ring-2 focus-visible:ring-ring",
            "disabled:opacity-50",
          )}
        />
        <div className="flex items-center justify-between gap-2">
          {error ? (
            <p
              role="alert"
              data-testid="case-detail-note-error"
              className="text-xs text-destructive"
            >
              {formatApiError(error, "Couldn’t add note")}
            </p>
          ) : (
            <span aria-hidden />
          )}
          <Button
            type="submit"
            size="sm"
            disabled={disabled || pending || text.trim().length === 0}
            data-testid="case-detail-note-submit"
          >
            {pending ? "Adding…" : "Add note"}
          </Button>
        </div>
      </form>

      {notes.length === 0 ? (
        <p
          data-testid="case-detail-notes-empty"
          className="rounded-md border border-dashed border-border p-6 text-center text-sm text-muted-foreground"
        >
          No notes yet.
        </p>
      ) : (
        <ul className="space-y-2" data-testid="case-detail-notes-list">
          {notes.map((n) => (
            <li
              key={n.id}
              className="rounded-md border border-border bg-card p-3"
              data-testid={`case-detail-note-${n.id}`}
            >
              <div className="flex items-center justify-between text-[11px] text-muted-foreground">
                <span>{n.actor_user_id ?? "system"}</span>
                <span>{formatDateTime(n.occurred_at)}</span>
              </div>
              <div className="mt-1 whitespace-pre-wrap text-sm">
                {typeof n.payload.text === "string"
                  ? n.payload.text
                  : JSON.stringify(n.payload)}
              </div>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Activity tab
// ---------------------------------------------------------------------------

function ActivityTab({ audit }: { audit: readonly AuditEntry[] }) {
  if (audit.length === 0) {
    return (
      <p
        data-testid="case-detail-activity-empty"
        className="rounded-md border border-dashed border-border p-6 text-center text-sm text-muted-foreground"
      >
        No activity recorded yet.
      </p>
    );
  }
  return (
    <ul
      data-testid="case-detail-activity"
      className="space-y-2"
    >
      {audit.map((a) => (
        <li
          key={a.id}
          className="rounded-md border border-border bg-card p-3"
          data-testid={`case-detail-activity-${a.id}`}
        >
          <div className="flex items-center justify-between text-[11px] text-muted-foreground">
            <span className="font-mono">{a.action}</span>
            <span>{formatDateTime(a.occurred_at)}</span>
          </div>
          <div className="mt-1 break-all text-[11px] text-muted-foreground">
            actor: {a.actor_user_id ?? "system"}
          </div>
          {Object.keys(a.payload).length > 0 ? (
            <pre className="mt-1 overflow-x-auto whitespace-pre-wrap break-all text-[11px] text-muted-foreground">
              {JSON.stringify(a.payload, null, 0)}
            </pre>
          ) : null}
        </li>
      ))}
    </ul>
  );
}

// ---------------------------------------------------------------------------
// States
// ---------------------------------------------------------------------------

function LoadingState() {
  return (
    <div className="space-y-4" data-testid="case-detail-loading">
      <div className="h-4 w-32 animate-pulse rounded bg-muted" />
      <div className="h-8 w-64 animate-pulse rounded bg-muted" />
      <div className="h-32 w-full animate-pulse rounded bg-muted" />
    </div>
  );
}

function NotFoundState() {
  return (
    <div className="space-y-4" data-testid="case-detail-not-found">
      <p className="text-lg font-medium">Case not found.</p>
      <Link
        to="/cases"
        className="text-sm text-primary underline-offset-2 hover:underline"
      >
        ← Back to cases
      </Link>
    </div>
  );
}

function ErrorState({
  error,
  onRetry,
}: {
  error: unknown;
  onRetry: () => void;
}) {
  return (
    <div
      role="alert"
      data-testid="case-detail-error"
      className="flex flex-col items-start gap-2 rounded-md border border-destructive/50 bg-destructive/5 p-4 text-sm"
    >
      <p className="font-medium text-destructive">
        {formatApiError(error, "Couldn’t load case")}.
      </p>
      <Button variant="outline" size="sm" onClick={onRetry}>
        Retry
      </Button>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

const selectCn = cn(
  "h-9 w-full rounded-md border border-input bg-background px-3 text-sm",
  "focus:outline-none focus-visible:ring-2 focus-visible:ring-ring",
  "disabled:opacity-50",
);

function formatApiError(err: unknown, prefix: string): string {
  if (err instanceof ApiError) return `${prefix} (${err.status}): ${err.detail}`;
  if (err instanceof Error) return `${prefix}: ${err.message}`;
  return prefix;
}
