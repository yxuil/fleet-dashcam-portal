/**
 * Playwright global setup — runs once before any test.
 *
 * Re-seeds the backend database so every run starts from a deterministic
 * fixture: 2 tenants, 4 users, 6 trucks, 8 drivers, ~200 clips, ~80 events.
 * `--no-upload-samples` skips MinIO uploads — playback URLs still get minted
 * (the backend signs a URL regardless of whether the object exists), and the
 * smoke test only asserts the `<video>` element has a MinIO-pointing `src`,
 * not that bytes actually load.
 *
 * The seed script runs from `backend/` via `uv run` so it picks up the
 * project's pinned Python deps. We shell out via `spawnSync` rather than
 * importing anything Python-y; this keeps the Node side dependency-free.
 *
 * If the backend dir isn't reachable (e.g. tests run from a CI container
 * without the Python toolchain) we surface the error loudly so the dev knows
 * to seed manually before invoking `playwright test`.
 */

import { spawnSync } from "node:child_process";
import { existsSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

// ESM doesn't expose `__dirname`; derive it from `import.meta.url` so the
// resolved BACKEND_DIR stays correct no matter where playwright is invoked.
const __dirname = path.dirname(fileURLToPath(import.meta.url));

const BACKEND_DIR = path.resolve(__dirname, "../../../backend");

export default async function globalSetup(): Promise<void> {
  if (process.env.E2E_SKIP_SEED === "1") {
    // Escape hatch for iterating on tests without re-paying the seed cost.
    // eslint-disable-next-line no-console
    console.log("[e2e:setup] E2E_SKIP_SEED=1 — skipping reseed.");
    return;
  }

  if (!existsSync(BACKEND_DIR)) {
    throw new Error(
      `[e2e:setup] backend directory not found at ${BACKEND_DIR}. ` +
        `Run with E2E_SKIP_SEED=1 to bypass if you've seeded manually.`,
    );
  }

  // eslint-disable-next-line no-console
  console.log("[e2e:setup] reseeding backend via `uv run python -m app.seed --reset --no-upload-samples`…");
  const result = spawnSync(
    "uv",
    ["run", "python", "-m", "app.seed", "--reset", "--no-upload-samples"],
    {
      cwd: BACKEND_DIR,
      stdio: "inherit",
      env: process.env,
    },
  );

  if (result.error) {
    throw new Error(
      `[e2e:setup] failed to spawn 'uv run …': ${result.error.message}. ` +
        `Is uv installed and on PATH?`,
    );
  }
  if (result.status !== 0) {
    throw new Error(
      `[e2e:setup] seed exited with status ${result.status}. See log above.`,
    );
  }
  // eslint-disable-next-line no-console
  console.log("[e2e:setup] reseed complete.");
}
