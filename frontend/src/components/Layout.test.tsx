import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { Layout } from "./Layout";

// Mock the API wrapper so the layout renders /me content without a real
// backend round-trip.  The test mounts the layout under MemoryRouter so
// NavLink resolves without a real history.
vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return {
    ...actual,
    apiGet: vi.fn(),
  };
});

import { apiGet } from "@/lib/api";

function renderLayout() {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={["/dashcam"]}>
        <Routes>
          <Route path="/" element={<Layout />}>
            <Route path="dashcam" element={<div>Fleet Cam content</div>} />
          </Route>
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("Layout", () => {
  beforeEach(() => {
    vi.mocked(apiGet).mockReset();
  });

  afterEach(() => {
    vi.mocked(apiGet).mockReset();
  });

  it("renders shell with app title and nav links", async () => {
    vi.mocked(apiGet).mockResolvedValue({
      user_id: "u",
      tenant_id: "a6190065-514d-5d27-b599-e81673beb843",
      roles: ["viewer"],
      email: "viewer@acme.dev",
      name: "Acme Logistics Viewer",
    });

    renderLayout();

    expect(screen.getByText("Dashcam Portal")).toBeInTheDocument();
    expect(
      screen.getByRole("link", { name: "Fleet Cam" }),
    ).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Trucks" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Cases" })).toBeInTheDocument();
    expect(screen.getByText("Fleet Cam content")).toBeInTheDocument();
  });

  it("shows Loading… until /me resolves, then the user name", async () => {
    let resolve: (value: unknown) => void = () => {};
    vi.mocked(apiGet).mockReturnValue(
      new Promise((res) => {
        resolve = res;
      }),
    );

    renderLayout();

    expect(screen.getByTestId("user-name")).toHaveTextContent("Loading…");

    resolve({
      user_id: "u",
      tenant_id: "a6190065-514d-5d27-b599-e81673beb843",
      roles: ["admin"],
      email: "admin@acme.dev",
      name: "Acme Logistics Admin",
    });

    await waitFor(() => {
      expect(screen.getByTestId("user-name")).toHaveTextContent(
        "Acme Logistics Admin",
      );
    });
    expect(screen.getByTestId("tenant-name")).toHaveTextContent(
      "Acme Logistics",
    );
  });
});
