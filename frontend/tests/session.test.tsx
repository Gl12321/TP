import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { expect, it, vi } from "vitest";
import { SessionProvider, useSession } from "../src/app/session";
import type { Session } from "../src/shared/api/contracts";

const session: Session = {
  user: { id: "u", email: "test@example.test", name: "Сотрудник" },
  csrf_token: "csrf",
  workspaces: [],
};

function Consumer() {
  const { session: current, loading, authenticate, logout } = useSession();
  return (
    <>
      <span>{loading ? "loading" : (current?.user.name ?? "guest")}</span>
      <button onClick={() => authenticate(session)}>login</button>
      <button onClick={() => void logout()}>logout</button>
    </>
  );
}

it("updates the mounted session observer on login and logout while clearing private cache", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn().mockImplementation((url: string) =>
      Promise.resolve(
        new Response(
          url.includes("/session")
            ? '{"error":{"code":"unauthorized","message":"Login"}}'
            : '{"ok":true}',
          {
            status: url.includes("/session") ? 401 : 200,
            headers: { "content-type": "application/json" },
          },
        ),
      ),
    ),
  );
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  client.setQueryData(["workspace", "old", "runs"], ["old-private-result"]);
  render(
    <QueryClientProvider client={client}>
      <SessionProvider>
        <Consumer />
      </SessionProvider>
    </QueryClientProvider>,
  );
  await screen.findByText("guest");
  const user = userEvent.setup();
  await user.click(screen.getByText("login"));
  await screen.findByText("Сотрудник");
  expect(client.getQueryData(["workspace", "old", "runs"])).toBeUndefined();
  client.setQueryData(["workspace", "new", "runs"], ["private-result"]);
  await user.click(screen.getByText("logout"));
  await waitFor(() => expect(screen.getByText("guest")).toBeVisible());
  expect(client.getQueryData(["workspace", "new", "runs"])).toBeUndefined();
});
