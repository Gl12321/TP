import { expect, it, vi } from "vitest";
import {
  get,
  post,
  setCsrfToken,
  workspacePath,
} from "../src/shared/api/client";

it("sends cookies and CSRF with writes into the explicit workspace", async () => {
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
  setCsrfToken("");
});

it("signals revoked access while retaining the server error", async () => {
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
  try {
    await expect(get("/workspaces/w/runs/r")).rejects.toMatchObject({
      status: 403,
      code: "scope_forbidden",
    });
    expect(listener).toHaveBeenCalledOnce();
  } finally {
    window.removeEventListener("access-revoked", listener);
  }
});
