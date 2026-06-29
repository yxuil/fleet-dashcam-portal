import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError, UNAUTHORIZED_EVENT, apiGet, apiPost } from "./api";
import { API_BASE } from "./env";

// Mock `getAuthHeaders` so the tests don't depend on localStorage state.
vi.mock("./auth", () => ({
  getAuthHeaders: () => ({
    "X-Dev-User-Id": "user-123",
    "X-Dev-Tenant-Id": "tenant-456",
  }),
}));

type FetchMock = ReturnType<typeof vi.fn>;

function jsonResponse(body: unknown, init: { status?: number } = {}): Response {
  return new Response(JSON.stringify(body), {
    status: init.status ?? 200,
    headers: { "content-type": "application/json" },
  });
}

describe("api wrapper", () => {
  let fetchMock: FetchMock;

  beforeEach(() => {
    fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it("attaches auth headers and prefixes the API base on GET", async () => {
    fetchMock.mockResolvedValue(jsonResponse({ ok: true }));

    const result = await apiGet<{ ok: boolean }>("/me");
    expect(result).toEqual({ ok: true });

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe(`${API_BASE}/me`);
    expect(init.method).toBe("GET");
    const headers = init.headers as Headers;
    expect(headers.get("X-Dev-User-Id")).toBe("user-123");
    expect(headers.get("X-Dev-Tenant-Id")).toBe("tenant-456");
    expect(headers.get("Accept")).toBe("application/json");
  });

  it("serialises JSON bodies and sets Content-Type for POST", async () => {
    fetchMock.mockResolvedValue(jsonResponse({ id: 1 }, { status: 201 }));

    await apiPost("/cases", { name: "n" });

    const [, init] = fetchMock.mock.calls[0];
    expect(init.method).toBe("POST");
    expect(init.body).toBe(JSON.stringify({ name: "n" }));
    const headers = init.headers as Headers;
    expect(headers.get("Content-Type")).toBe("application/json");
  });

  it("throws ApiError with the FastAPI detail on non-2xx", async () => {
    fetchMock.mockResolvedValue(
      new Response(JSON.stringify({ detail: "forbidden — try again" }), {
        status: 403,
        headers: { "content-type": "application/json" },
      }),
    );

    await expect(apiGet("/cases")).rejects.toMatchObject({
      status: 403,
      detail: "forbidden — try again",
    });
    await expect(apiGet("/cases")).rejects.toBeInstanceOf(ApiError);
  });

  it("dispatches auth:unauthorized on 401", async () => {
    fetchMock.mockResolvedValue(
      new Response(JSON.stringify({ detail: "nope" }), {
        status: 401,
        headers: { "content-type": "application/json" },
      }),
    );
    const handler = vi.fn();
    window.addEventListener(UNAUTHORIZED_EVENT, handler);

    await expect(apiGet("/me")).rejects.toMatchObject({ status: 401 });
    expect(handler).toHaveBeenCalledTimes(1);

    window.removeEventListener(UNAUTHORIZED_EVENT, handler);
  });

  it("returns undefined for 204 No Content", async () => {
    fetchMock.mockResolvedValue(new Response(null, { status: 204 }));

    const out = await apiGet<void>("/events/abc/triage");
    expect(out).toBeUndefined();
  });
});
