/**
 * Telemetry formatter — shared by `EventRow.tsx` and its tests.
 *
 * Pulled out of the row component so oxlint's `only-export-components`
 * rule stays happy (mixing component and utility exports breaks
 * Vite's fast-refresh boundary).
 */

/**
 * Pick a short "speed 64km/h, ΔG 1.2" style snippet from the telemetry blob.
 * Only the two most-likely-useful fields are surfaced; anything else is
 * hidden behind the clip page's detail view.
 */
export function formatTelemetrySnippet(
  telemetry: Record<string, unknown>,
): string {
  const parts: string[] = [];
  const speed = telemetry.speed_kmh;
  if (typeof speed === "number" && Number.isFinite(speed)) {
    parts.push(`speed ${speed.toFixed(0)} km/h`);
  }
  const accel = telemetry.accel_g;
  if (typeof accel === "number" && Number.isFinite(accel)) {
    parts.push(`ΔG ${accel.toFixed(1)}`);
  }
  return parts.join(", ");
}
