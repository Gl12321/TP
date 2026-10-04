import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import type { ReactNode } from "react";
import { describe, expect, it, vi } from "vitest";
import { WorkspaceProvider } from "../src/app/workspace";
import type { Run, Workspace } from "../src/shared/api/contracts";
import { ResultPanel } from "../src/shared/results/ResultPanel";
import { ToastProvider } from "../src/shared/ui/Toast";
import { RunCard } from "../src/features/assistant/RunCard";
import { DataTable } from "../src/shared/results/DataTable";

const workspace: Workspace = {
  id: "workspace",
  name: "Сеть",
  role: "store_manager",
  capabilities: ["analytics:read"],
  all_stores: false,
  store_ids: ["store"],
};
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

function mount(children: ReactNode) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <WorkspaceProvider workspace={workspace}>
          <ToastProvider>{children}</ToastProvider>
        </WorkspaceProvider>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("result provenance and data scope", () => {
  it("shows exact decimals, partial export wording and attached SQL", async () => {
    const user = userEvent.setup();
    mount(<ResultPanel run={run} />);
    expect(
      screen.getByText(
        "9\u202f007\u202f199\u202f254\u202f740\u202f993,123456789",
        { normalizer: (value) => value },
      ),
    ).toBeVisible();
    expect(
      screen.getByText("Это часть результата, не полный итог"),
    ).toBeVisible();
    expect(
      screen.getByRole("button", { name: "Скачать показанные строки" }),
    ).toBeEnabled();
    expect(
      screen.queryByRole("button", { name: "Сохранить отчёт" }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Создать разбор" }),
    ).not.toBeInTheDocument();
    await user.click(screen.getByText("SQL и основание расчёта"));
    expect(screen.getByText(run.sql!)).toBeVisible();
    await user.click(
      screen.getByText("Выполненный запрос и ограничения доступа"),
    );
    expect(screen.getByText(run.result!.execution!.sql)).toBeVisible();
    expect(screen.getByText(/STORE-1/)).toBeVisible();
  });
  it("does not retain a cached SQL table on revoked read access", async () => {
    vi.stubGlobal(
      "fetch",
      vi
        .fn()
        .mockImplementation(() =>
          Promise.resolve(
            new Response(
              '{"error":{"code":"scope_forbidden","message":"Область недоступна"}}',
              { status: 403, headers: { "content-type": "application/json" } },
            ),
          ),
        ),
    );
    mount(<RunCard initial={run} />);
    await waitFor(() =>
      expect(screen.getByText("Доступ ограничен")).toBeVisible(),
    );
    expect(screen.queryByText(run.sql!)).not.toBeInTheDocument();
    expect(
      screen.queryByText(
        "9\u202f007\u202f199\u202f254\u202f740\u202f993,123456789",
        { normalizer: (value) => value },
      ),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Скачать показанные строки" }),
    ).not.toBeInTheDocument();
  });
  it("makes loaded result pages navigable by keyboard-accessible controls", async () => {
    const user = userEvent.setup();
    render(
      <DataTable
        rows={[{ name: "Первая" }, { name: "Вторая" }, { name: "Третья" }]}
        columns={[{ accessorKey: "name", header: "Точка" }]}
        caption="Точки"
        pageSize={2}
      />,
    );
    expect(screen.getByText("Первая")).toBeVisible();
    expect(screen.queryByText("Третья")).not.toBeInTheDocument();
    await user.click(
      screen.getByRole("button", { name: "Следующая страница" }),
    );
    expect(screen.getByText("Третья")).toBeVisible();
    expect(
      screen.getByRole("button", { name: "Следующая страница" }),
    ).toBeDisabled();
    expect(screen.getByText("3–3 из 3 загруженных строк")).toBeVisible();
  });
});
