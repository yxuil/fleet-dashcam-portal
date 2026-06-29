/**
 * Vitest setup file — loaded once per test file via `vite.config.ts`.
 *
 * - Wires `@testing-library/jest-dom` matchers onto `expect`.
 * - Cleans up React Testing Library renders after each test (Vitest
 *   doesn't do this automatically the way Jest's setup did).
 */

import "@testing-library/jest-dom/vitest";

import { cleanup } from "@testing-library/react";
import { afterEach } from "vitest";

afterEach(() => {
  cleanup();
});
