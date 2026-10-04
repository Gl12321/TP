import { describe, expect, it, vi } from "vitest";
import {
  ApiError,
  get,
  post,
  queryString,
  setCsrfToken,
  workspacePath,
} from "../src/shared/api/client";

describe("API boundary", () => {
  it("sends CSRF on writes, includes cookies and respects explicit workspace", async () => {
    const fetch = vi.fn().mockResolvedValue(
      new Response('{"ok":true}', {
        headers: { "content-type": "application/json" },
      }),
    );
    vi.stubGlobal("fetch", fetch);
    setCsrfToken("csrf-test");
    await post(workspacePath("id/with slash", "/plans"), {
      amount: "9007199254740993.12",
    });
    const [path, options] = fetch.mock.calls[0];
    expect(path).toBe("/api/v1/workspaces/id%2Fwith%20slash/plans");
    expect(options.credentials).toBe("include");
    expect(options.headers.get("X-CSRF-Token")).toBe("csrf-test");
    expect(options.body).toContain("9007199254740993.12");
  });
  it("signals revoked access and surfaces server reason", async () => {
    const listener = vi.fn();
    window.addEventListener("access-revoked", listener);
    vi.stubGlobal(
      "fetch",
      vi
        .fn()
        .mockResolvedValue(
          new Response(
            '{"error":{"code":"scope_forbidden","message":"Область недоступна"}}',
            { status: 403, headers: { "content-type": "application/json" } },
          ),
        ),
    );
    await expect(get("/workspaces/w/runs/r")).rejects.toMatchObject({
      status: 403,
      code: "scope_forbidden",
    });
    expect(listener).toHaveBeenCalledOnce();
    window.removeEventListener("access-revoked", listener);
  });
  it("rejects HTML returned in place of JSON", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response("<html>login</html>", {
          headers: { "content-type": "text/html" },
        }),
      ),
    );
    await expect(get("/runs/r")).rejects.toBeInstanceOf(ApiError);
  });
  it("keeps cancellation distinguishable from a network failure", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockRejectedValue(new DOMException("Cancelled", "AbortError")),
    );
    await expect(get("/runs/r")).rejects.toMatchObject({ name: "AbortError" });
  });
  it("encodes filters without ambiguous query separators", () =>
    expect(
      queryString({ store_ids: ["a", "b"], metric_id: "a&b", absent: null }),
    ).toBe("?store_ids=a%2Cb&metric_id=a%26b"));
});
