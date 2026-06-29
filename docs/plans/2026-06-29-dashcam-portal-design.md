# Fleet Dashcam Portal — Feature Design

**Date:** 2026-06-29
**Status:** Validated design, ready for implementation planning

## Context

The portal is the customer-facing web app for a truck fleet dashcam system. Each tenant (fleet operator) has many trucks; each truck uploads dashcam video continuously (live or nightly). User login is handled by an existing system upstream — this design covers everything after authentication.

**Primary user:** compliance / insurance staff inside the fleet operator. The job-to-be-done is **evidence retrieval** — pulling clips and supporting metadata for claims, disputes, DOT audits, and litigation.

**Workflow mix:** most pulls are quick internal searches by the customer's own staff; a smaller share are formal, high-stakes requests (subpoena, litigation, major claim) that need approval and chain-of-custody.

**Storage model:** continuous upload with ~30-day default retention; event clips kept ~90 days; Legal Hold extends retention indefinitely for in-scope footage.

## Top-level Information Architecture

Five primary areas:

1. **Search & Browse** — landing page. Find footage by truck, driver, date-time range, geofence/map area, event type, severity. Result grid with thumbnails.
2. **Event Timeline** — per-truck or per-driver chronological feed of auto-flagged events (harsh brake/accel, collision, lane departure, speeding, distracted-driving cue). Filterable by severity and event type.
3. **Cases** — formal request workflow. Wraps clips with claim/case number, requester info, status, an evidence locker, and an immutable audit log.
4. **Legal Holds** — list of holds that extend retention past the default. Each names a scope (trucks/drivers/date-range or saved search), reason, owner, expiry.
5. **Admin** — per-tenant settings: users & RBAC, retention policy, truck/driver roster sync, dashcam health, audit-log export.

## Core Workflows

### A. Quick internal pull (80% case)
Search → filter by truck + date → scan result grid → open clip → scrub with telemetry overlay → download MP4 (watermarked) or copy a clip URL. Every action audit-logged. Sub-minute interaction.

### B. Event review (proactive triage)
Open Event Timeline → filter by severity → triage each event with one of: *false positive*, *coaching note*, *open case*. The "open case" action seeds a new Case with the clip pre-attached.

### C. Formal case (high-stakes pull)
Create Case (claim #, requester, due date) → attach clips → apply Legal Hold to underlying footage → assign reviewer → approve → generate chain-of-custody package (clips + telemetry + SHA-256 hashes + audit-log PDF). Deliver via watermarked, time-limited, password-protected share link **or** sealed ZIP download.

## Feature Detail per Area

### Search & Browse
- **Filters:** tenant (auto-scoped), trucks, drivers, date-time range, event type, severity, location (polygon or saved geofence)
- **Results:** thumbnail grid with truck/driver/timestamp/event tag/duration
- **Saved searches** and shareable in-portal URLs

### Video Player
- Multi-camera switching (road / cabin / side) when available
- Telemetry overlay: speed, accel/decel, GPS pin on inset map, harsh-event markers on the scrubber
- Frame-step (←/→), shuttle speed 0.5x–4x, bookmark moments, clip-and-trim to export sub-segments
- Every play / scrub / export logged

### Event Timeline
- Severity buckets (critical / high / medium / low) and event-type chips
- Inline triage: *false positive*, *coaching note*, *open case*
- Trend strip: events-per-day for the selected truck/driver

### Cases
- Header: case #, external ref #, requester (name + org), incident date, status (*open / under review / approved / closed*), assignee, due date
- Tabs: **Evidence** (clips + telemetry), **Activity** (audit log), **Notes** (internal-only), **Hold** (on/off)
- Actions: add clip from search, generate share link, export sealed ZIP, close with reason

### Legal Holds
- Scope: trucks + drivers + date-range, OR a saved search, OR "all clips in case X"
- Owner, reason, expiry; renewable; releases require justification

### Admin
- Users + RBAC: **viewer / reviewer / case-manager / admin**
- Retention policy (default 30d continuous / 90d events; per-tenant override)
- Dashcam health: last-seen, upload backlog, storage health
- Audit log: searchable, exportable as CSV/PDF

## Cross-cutting Concerns

**Tenant isolation.** Every record keyed by `tenant_id`. All queries scope to caller's tenant. Storage paths and signed-URL prefixes include `tenant_id`. Support impersonation is itself audit-logged.

**Chain of custody.** On ingest each clip gets a SHA-256 hash, ingest timestamp, dashcam serial, and firmware version — stored immutably. Exports bundle these as a manifest. Audit log is append-only and exportable as a signed PDF.

**Watermarking.** Every in-portal playback and every exported file carries viewer name + email + timestamp + case # (when in case context).

**Retention & deletion.** Default TTL: 30d continuous, 90d events. Legal Hold exempts matching footage. Deletion is soft-delete + 7-day grace before hard-delete.

**External sharing.** Time-limited (default 7d), optionally password-protected, view-only by default, per-recipient watermark, every external view logged.

**Notifications.** In-app + email for: new high-severity event, case assigned to me, case due-date approaching, legal hold expiring, retention-imminent warning on case-linked clips.

**Ops observability** (internal support, hidden role). Per-tenant dashboard: upload backlog, storage usage, dashcam offline counts, ingest error rate.

## Explicitly Out of Scope (for this design)

- Live ops / real-time fleet map and live video peek-in — different primary user (dispatcher), not compliance
- Driver scorecards and gamification — safety-coaching feature set
- Driver-facing mobile app
- Authentication, SSO, identity provisioning — handled upstream
- Billing, subscription management
- Camera firmware OTA management

## Open Questions for Implementation Planning

- Tech stack (frontend framework, backend language, video storage backend)
- Existing services this portal will integrate with for truck/driver roster, event detection, ingest pipeline
- Compliance/regulatory requirements (FMCSA, GDPR, state-specific) that may shape retention and audit details
- Performance targets: how large is a typical tenant (trucks, daily upload volume), and concurrent users?
- Whether to ship the formal Case workflow in MVP or follow-on (quick search + event timeline could ship first)
