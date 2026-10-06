import {
  act,
  render,
  renderHook,
  screen,
  waitFor,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import type { ReactNode } from "react";
import { expect, it, vi } from "vitest";
import { WorkspaceProvider } from "../src/app/workspace";
import type { Run, Workspace } from "../src/shared/api/contracts";
import { ResultPanel } from "../src/shared/results/ResultPanel";
import { ToastProvider } from "../src/shared/ui/Toast";
import { useSubmissionKey } from "../src/shared/ui/useSubmissionKey";
import { RunCard } from "../src/features/assistant/components/RunCard";

const workspace: Workspace = {
  id: "workspace",
  name: "Сеть",
  role: "store_manager",
  capabilities: ["analytics:read"],
  all_stores: false,
  store_ids: ["store"],
};
const exactValue = "9\u202f007\u202f199\u202f254\u202f740\u202f993,123456789";
const run: Run = {
  id: "run",
  conversation_id: "conversation",
  question: "Продажи",
  source_id: "source",
  store_ids: ["store"],
  status: "succeeded",
  stage: "done",
  sql: 'SELECT "amount" FROM "reporting"."orders";',
  result: {
    columns: [{ name: "amount", type: "numeric" }],
    rows: [["9007199254740993.123456789"]],
    row_count: 1,
    truncated: true,
    execution: {
      sql: 'SELECT "amount" FROM "reporting"."orders" WHERE "store_code" = ANY($1::text[])',
      parameters: [["STORE-1"]],
    },
  },
  error: null,
  clarification: null,
  created_at: "2026-10-01T10:00:00Z",
  finished_at: "2026-10-01T10:00:05Z",
  conversation_version: 1,
};

function providers() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <WorkspaceProvider workspace={workspace}>
          <ToastProvider>{children}</ToastProvider>
        </WorkspaceProvider>
      </MemoryRouter>
    </QueryClientProvider>
  );
}

it("shows precise results, partial export and the executed SQL with its scope", async () => {
  const user = userEvent.setup();
  render(<ResultPanel run={run} />, { wrapper: providers() });
  expect(screen.getByText(exactValue, { normalizer: (v) => v })).toBeVisible();
  expect(
    screen.getByText("Это часть результата, не полный итог"),
  ).toBeVisible();
  expect(
    screen.getByRole("button", { name: "Скачать показанные строки" }),
  ).toBeEnabled();
  await user.click(screen.getByText("SQL и основание расчёта"));
  expect(screen.getByText(run.sql!)).toBeVisible();
  await user.click(
    screen.getByText("Выполненный запрос и ограничения доступа"),
  );
  expect(screen.getByText(run.result!.execution!.sql)).toBeVisible();
  expect(screen.getByText(/STORE-1/)).toBeVisible();
});

it("removes a cached table when the server revokes access", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn().mockImplementation(() =>
      Promise.resolve(
        new Response(
          '{"error":{"code":"scope_forbidden","message":"Область недоступна"}}',
          {
            status: 403,
            headers: { "content-type": "application/json" },
          },
        ),
      ),
    ),
  );
  render(<RunCard initial={run} />, { wrapper: providers() });
  await waitFor(() =>
    expect(screen.getByText("Доступ ограничен")).toBeVisible(),
  );
  expect(screen.queryByText(run.sql!)).not.toBeInTheDocument();
  expect(
    screen.queryByText(exactValue, { normalizer: (v) => v }),
  ).not.toBeInTheDocument();
  expect(
    screen.queryByRole("button", { name: "Скачать показанные строки" }),
  ).not.toBeInTheDocument();
});

it("reuses a submission key after reopening but replaces it for edited or completed work", () => {
  const wrapper = providers();
  const body = { title: "Продажи", run_id: "run" };
  const first = renderHook(() => useSubmissionKey("save-report:run"), {
    wrapper,
  });
  let initial = "";
  act(() => {
    initial = first.result.current.keyFor(body);
  });
  first.unmount();
  const reopened = renderHook(() => useSubmissionKey("save-report:run"), {
    wrapper,
  });
  act(() => expect(reopened.result.current.keyFor(body)).toBe(initial));
  act(() =>
    expect(
      reopened.result.current.keyFor({ ...body, title: "Расходы" }),
    ).not.toBe(initial),
  );
  let edited = "";
  act(() => {
    edited = reopened.result.current.keyFor(body);
  });
  act(() => reopened.result.current.clearKey());
  act(() => expect(reopened.result.current.keyFor(body)).not.toBe(edited));
});
