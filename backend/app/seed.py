"""CLI to populate the dev database with realistic data.

Usage::

    python -m app.seed --reset
    python -m app.seed --reset --no-upload-samples

What it does
------------
* Creates two tenants (``Acme Logistics`` and ``Northwind Freight``) with
  deterministic ``uuid5`` ids so the frontend can hardcode them during dev.
* For each tenant: 1 admin user, 1 viewer user, 3 trucks, 4 drivers.
* ~200 clips total spread randomly over the last 30 days. Each clip
  references one of the sample MP4s round-robin (or a synthetic key if
  ``samples/`` is empty).
* ~80 events with a mix of types and severities, each anchored to a real
  clip on the same truck.

No cases are seeded — keep MVP seed simple. Frontend (T13) creates them.

Determinism
-----------
A fixed-seed :class:`random.Random` drives every random choice, so two
runs produce identical row contents (UUIDs, timestamps, telemetry). This
makes screenshot/diff testing tolerable.

Idempotency
-----------
The CLI is **not** idempotent without ``--reset`` — a second run will
violate uniqueness (e.g. ``uq_users_tenant_email``). With ``--reset``,
re-running truncates everything first and reproduces the same dataset.

Programmatic entry
------------------
:func:`run_seed` is the test/automation entry point; the ``__main__``
block just parses flags and delegates to it.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import random
import sys
import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.config import settings
from app.models.clip import Clip
from app.models.driver import Driver
from app.models.event import Event, EventSeverity, EventType
from app.models.tenant import Tenant
from app.models.truck import Truck
from app.models.user import User
from app.storage import build_clip_key

logger = logging.getLogger("app.seed")


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: Deterministic seed for the in-script :class:`random.Random` instance.
RANDOM_SEED: int = 0xDA56CA4

#: Namespace for ``uuid5`` derivation of stable tenant ids.
_DNS = uuid.NAMESPACE_DNS

#: Tables to truncate on ``--reset``, ordered to respect FK dependencies.
#: Audit first (no FK in either direction in practice), then join tables,
#: then leaf rows, then the parents.
TABLES_TO_RESET: tuple[str, ...] = (
    "audit_log",
    "case_clips",
    "cases",
    "events",
    "clips",
    "drivers",
    "trucks",
    "users",
    "tenants",
)

#: Bounding box for synthetic GPS coords — somewhere in the US Midwest.
_GPS_LAT_RANGE = (41.5, 42.5)
_GPS_LNG_RANGE = (-88.5, -87.0)


@dataclass(frozen=True)
class TenantSpec:
    """Inputs to seed one tenant + its trucks/drivers/users."""

    slug: str
    name: str
    truck_labels: tuple[str, ...]
    driver_names: tuple[str, ...]

    @property
    def tenant_id(self) -> uuid.UUID:
        # Stable across runs so frontend devs can paste IDs into URLs.
        return uuid.uuid5(_DNS, f"{self.slug}.dashcam")


TENANT_SPECS: tuple[TenantSpec, ...] = (
    TenantSpec(
        slug="acme",
        name="Acme Logistics",
        truck_labels=("Truck 101", "Truck 102", "Truck 103"),
        driver_names=("Alex Chen", "Brianna Davis", "Carlos Ortiz", "Dana Patel"),
    ),
    TenantSpec(
        slug="northwind",
        name="Northwind Freight",
        truck_labels=("Truck 201", "Truck 202", "Truck 203"),
        driver_names=("Erin Murphy", "Felix Tanaka", "Gwen Russo", "Henry Park"),
    ),
)

#: Total clip count to spread across both tenants (approximately).
TOTAL_CLIPS: int = 200

#: Total event count to spread across both tenants (approximately).
TOTAL_EVENTS: int = 80

#: How many days into the past to spread clips/events over.
WINDOW_DAYS: int = 30


@dataclass
class SeedSummary:
    """Row counts produced by a single seed run."""

    tenants: int = 0
    users: int = 0
    trucks: int = 0
    drivers: int = 0
    clips: int = 0
    events: int = 0
    uploaded_samples: int = 0

    def as_line(self) -> str:
        return (
            f"Seed complete: tenants={self.tenants} "
            f"users={self.users} trucks={self.trucks} drivers={self.drivers} "
            f"clips={self.clips} events={self.events} "
            f"uploads={self.uploaded_samples}"
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _samples_dir() -> Path:
    """Return the path to the repo-root ``samples/`` directory.

    Computed from this file's location so the seed works regardless of cwd.
    Layout: ``<repo>/backend/app/seed.py`` -> ``<repo>/samples``.
    """
    return Path(__file__).resolve().parents[2] / "samples"


def _list_sample_mp4s() -> list[Path]:
    """Return any ``*.mp4`` files under ``samples/`` (sorted for stability)."""
    d = _samples_dir()
    if not d.is_dir():
        return []
    return sorted(p for p in d.glob("*.mp4") if p.is_file())


async def _reset(session: AsyncSession) -> None:
    """Truncate all app tables in FK-safe order."""
    # TRUNCATE CASCADE handles any FK we might've missed in ordering, but
    # we still list tables explicitly so accidental new tables aren't
    # silently nuked. RESTART IDENTITY resets the audit_log bigserial.
    table_list = ", ".join(TABLES_TO_RESET)
    await session.execute(
        text(f"TRUNCATE TABLE {table_list} RESTART IDENTITY CASCADE")
    )
    await session.flush()


def _random_dt(rng: random.Random, *, days_back: int, now: datetime) -> datetime:
    """Return a random datetime within the last ``days_back`` days.

    Drawn at 1-second granularity so we get unique-ish ``started_at``
    values without bothering with microsecond precision.
    """
    seconds = rng.randint(0, days_back * 24 * 3600 - 1)
    return now - timedelta(seconds=seconds)


def _round_robin(seq: list[Any], i: int) -> Any:
    return seq[i % len(seq)]


# ---------------------------------------------------------------------------
# Seed building blocks
# ---------------------------------------------------------------------------


def _build_tenant(spec: TenantSpec) -> Tenant:
    return Tenant(id=spec.tenant_id, name=spec.name)


def _build_users(spec: TenantSpec) -> list[User]:
    return [
        User(
            id=uuid.uuid5(_DNS, f"{spec.slug}.user.admin"),
            tenant_id=spec.tenant_id,
            email=f"admin@{spec.slug}.dev",
            name=f"{spec.name} Admin",
            roles=["admin"],
        ),
        User(
            id=uuid.uuid5(_DNS, f"{spec.slug}.user.viewer"),
            tenant_id=spec.tenant_id,
            email=f"viewer@{spec.slug}.dev",
            name=f"{spec.name} Viewer",
            roles=["viewer"],
        ),
    ]


def _build_trucks(spec: TenantSpec) -> list[Truck]:
    return [
        Truck(
            id=uuid.uuid5(_DNS, f"{spec.slug}.truck.{label}"),
            tenant_id=spec.tenant_id,
            label=label,
            vin=f"VIN-{spec.slug.upper()}-{i:04d}",
            dashcam_serial=f"DC-{spec.slug.upper()}-{i:06d}",
        )
        for i, label in enumerate(spec.truck_labels, start=1)
    ]


def _build_drivers(spec: TenantSpec) -> list[Driver]:
    return [
        Driver(
            id=uuid.uuid5(_DNS, f"{spec.slug}.driver.{name}"),
            tenant_id=spec.tenant_id,
            name=name,
            employee_ref=f"E-{spec.slug.upper()}-{i:04d}",
        )
        for i, name in enumerate(spec.driver_names, start=1)
    ]


def _build_clips(
    *,
    spec: TenantSpec,
    trucks: list[Truck],
    drivers: list[Driver],
    count: int,
    rng: random.Random,
    now: datetime,
) -> list[Clip]:
    """Return ``count`` clip rows for a single tenant.

    Trucks rotate round-robin (so each truck gets roughly the same number
    of clips) and drivers are picked at random. Storage keys use the
    canonical :func:`build_clip_key` layout — whether sample bytes are
    actually uploaded for those keys is decided later, by the upload step.
    """
    clips: list[Clip] = []
    for i in range(count):
        clip_id = uuid.uuid4()  # not deterministic — too many to bake stable ids
        truck = _round_robin(trucks, i)
        driver = rng.choice(drivers)
        started = _random_dt(rng, days_back=WINDOW_DAYS, now=now)
        duration = rng.randint(30, 180)

        # The storage_key is always the canonical layout — whether we have
        # real bytes for it (sample upload path) is decided at upload time
        # so that the DB row shape is identical with or without samples.
        storage_key = build_clip_key(spec.tenant_id, started, clip_id)

        clips.append(
            Clip(
                id=clip_id,
                tenant_id=spec.tenant_id,
                truck_id=truck.id,
                driver_id=driver.id,
                started_at=started,
                ended_at=started + timedelta(seconds=duration),
                duration_s=duration,
                storage_key=storage_key,
                sha256=None,  # not synthesizing realistic hashes
                dashcam_firmware=rng.choice(["1.4.2", "1.5.0", "1.5.1"]),
            )
        )
    return clips


def _build_events(
    *,
    spec: TenantSpec,
    clips: list[Clip],
    count: int,
    rng: random.Random,
) -> list[Event]:
    """Return ``count`` events anchored to clips on the same tenant.

    Each event picks a random clip, places ``occurred_at`` inside that
    clip's [started_at, ended_at] window, and inherits its truck. Half
    are ``harsh_brake`` (the typical event in real fleets), with the
    other half drawn from the rest.
    """
    if not clips:
        return []

    severities = list(EventSeverity)
    rare_types = [
        EventType.collision,
        EventType.distracted_driving,
        EventType.lane_departure,
        EventType.harsh_accel,
        EventType.speeding,
    ]

    events: list[Event] = []
    for _ in range(count):
        clip = rng.choice(clips)
        # Pick an event type: 50% harsh_brake, otherwise from the rare list.
        if rng.random() < 0.5:
            ev_type = EventType.harsh_brake
        else:
            ev_type = rng.choice(rare_types)

        # Severity skews lower; critical is rare for non-collision events.
        if ev_type is EventType.collision:
            severity = rng.choices(
                severities, weights=[6, 3, 1, 0], k=1
            )[0]  # critical heavy
        else:
            severity = rng.choices(
                severities, weights=[1, 2, 4, 3], k=1
            )[0]  # mostly medium/low

        window_s = max(1, clip.duration_s - 1)
        occurred_at = clip.started_at + timedelta(seconds=rng.randint(0, window_s))

        telemetry: dict[str, Any] = {
            "speed_kmh": rng.randint(40, 110),
            "accel_g": round(rng.uniform(-1.2, 1.2), 2),
        }
        if ev_type is EventType.speeding:
            telemetry["speed_limit_kmh"] = 90

        events.append(
            Event(
                id=uuid.uuid4(),
                tenant_id=spec.tenant_id,
                truck_id=clip.truck_id,
                clip_id=clip.id,
                occurred_at=occurred_at,
                type=ev_type,
                severity=severity,
                telemetry=telemetry,
                gps_lat=round(rng.uniform(*_GPS_LAT_RANGE), 5),
                gps_lng=round(rng.uniform(*_GPS_LNG_RANGE), 5),
            )
        )
    return events


# ---------------------------------------------------------------------------
# Sample upload (sync boto3 inside the seed — fine for non-latency-critical)
# ---------------------------------------------------------------------------


def _upload_samples_sync(
    clips: Iterable[Clip],
    sample_mp4s: list[Path],
) -> int:
    """PUT each clip's storage_key to MinIO using a round-robin sample MP4.

    Returns the number of objects successfully uploaded. Uploads are
    best-effort: if MinIO is down or a single PUT fails we log a warning
    and continue rather than tearing the whole seed down — the DB rows
    are still valid, they just won't have backing bytes.
    """
    from app import storage  # local import so test imports don't drag boto3

    try:
        storage._ensure_bucket_sync()
    except Exception:
        logger.exception("MinIO ensure_bucket failed; skipping uploads")
        return 0

    uploaded = 0
    # Cache file bytes so we don't re-read the same sample repeatedly.
    sample_bytes_cache: dict[Path, bytes] = {}

    for i, clip in enumerate(clips):
        sample_path = sample_mp4s[i % len(sample_mp4s)]
        body = sample_bytes_cache.get(sample_path)
        if body is None:
            body = sample_path.read_bytes()
            sample_bytes_cache[sample_path] = body

        try:
            storage._put_object_sync(clip.storage_key, body, "video/mp4")
            uploaded += 1
        except Exception:
            logger.exception(
                "failed to upload sample to %s — continuing",
                clip.storage_key,
            )
    return uploaded


# ---------------------------------------------------------------------------
# Programmatic entry point
# ---------------------------------------------------------------------------


async def run_seed(
    *,
    reset: bool = False,
    upload_samples: bool = True,
) -> SeedSummary:
    """Seed the database (and optionally MinIO) with dev data.

    Args:
        reset: If true, truncate all app tables first (FK-safe order).
        upload_samples: If true and ``samples/*.mp4`` exist, upload them to
            MinIO under each clip's storage_key. No-op if no samples.

    Returns:
        A :class:`SeedSummary` for printing / assertions.
    """
    rng = random.Random(RANDOM_SEED)
    now = datetime.now(tz=UTC)
    summary = SeedSummary()

    sample_mp4s = _list_sample_mp4s()
    if upload_samples and not sample_mp4s:
        logger.info(
            "No sample MP4s found in %s — DB rows will reference "
            "storage keys that aren't backed by real objects. "
            "Drop *.mp4 files in samples/ and rerun if you want playback.",
            _samples_dir(),
        )

    # Build a dedicated engine for this seed call. The shared engine in
    # ``app.db`` is bound to whatever event loop first touched it, which
    # makes ``run_seed`` painful to invoke from pytest where each test
    # spins up a fresh loop. Disposing here keeps the call self-contained.
    engine = create_async_engine(settings.database_url, pool_pre_ping=True, future=True)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    all_clips: list[Clip] = []
    try:
        async with factory() as session, session.begin():
            if reset:
                await _reset(session)

            for spec in TENANT_SPECS:
                # Tenant first so the children's FKs resolve on flush.
                session.add(_build_tenant(spec))
                await session.flush()

                users = _build_users(spec)
                trucks = _build_trucks(spec)
                drivers = _build_drivers(spec)
                session.add_all(users)
                session.add_all(trucks)
                session.add_all(drivers)
                await session.flush()

                summary.tenants += 1
                summary.users += len(users)
                summary.trucks += len(trucks)
                summary.drivers += len(drivers)

                # ~equal split of clips across tenants.
                clip_count = TOTAL_CLIPS // len(TENANT_SPECS)
                clips = _build_clips(
                    spec=spec,
                    trucks=trucks,
                    drivers=drivers,
                    count=clip_count,
                    rng=rng,
                    now=now,
                )
                session.add_all(clips)
                await session.flush()
                summary.clips += len(clips)
                all_clips.extend(clips)

                # ~equal split of events across tenants — anchor each event
                # to a clip within its own tenant.
                event_count = TOTAL_EVENTS // len(TENANT_SPECS)
                events = _build_events(
                    spec=spec,
                    clips=clips,
                    count=event_count,
                    rng=rng,
                )
                session.add_all(events)
                await session.flush()
                summary.events += len(events)

        # Upload samples *after* commit so we don't roll back DB rows when
        # MinIO is flaky. Uploads are best-effort.
        if upload_samples and sample_mp4s and all_clips:
            summary.uploaded_samples = await asyncio.to_thread(
                _upload_samples_sync, all_clips, sample_mp4s
            )
    finally:
        await engine.dispose()

    return summary


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m app.seed",
        description="Populate the dev database with realistic data.",
    )
    parser.add_argument(
        "--reset",
        action="store_true",
        help="Truncate all app tables before seeding (FK-safe order).",
    )
    upload = parser.add_mutually_exclusive_group()
    upload.add_argument(
        "--upload-samples",
        dest="upload_samples",
        action="store_true",
        default=True,
        help="Upload samples/*.mp4 to MinIO under each clip's storage_key (default).",
    )
    upload.add_argument(
        "--no-upload-samples",
        dest="upload_samples",
        action="store_false",
        help="Skip MinIO uploads; write DB rows only.",
    )
    return parser.parse_args(argv)


async def _main_async(args: argparse.Namespace) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    summary = await run_seed(
        reset=args.reset,
        upload_samples=args.upload_samples,
    )
    # ``run_seed`` disposes the engine it created internally, so the script
    # exits cleanly without dangling asyncpg connections.
    print(summary.as_line())
    return 0


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    return asyncio.run(_main_async(args))


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
