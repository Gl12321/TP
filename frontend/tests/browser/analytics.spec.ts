import { readFileSync } from "node:fs";
import { test, expect } from "@playwright/test";

const accountPath = process.env.APP_UI_CREDENTIALS;
const sourcePath = process.env.APP_UI_SOURCE;
test.skip(
  !accountPath || !sourcePath,
  "An isolated account and PostgreSQL fixture are required",
);
const account: { url: string; email: string; password: string } = accountPath
  ? JSON.parse(readFileSync(accountPath, "utf8"))
  : { url: "", email: "", password: "" };
type Fixture = {
  source: Record<string, unknown>;
  stores: { name: string; code: string; city: string; owner_name: string }[];
  policies: {
    tables: {
      schema: string;
      name: string;
      columns: string[];
      store_column: string | null;
      shared: boolean;
    }[];
  };
  metric: {
    key: string;
    name: string;
    description: string;
    unit: string;
    table_schema: string;
    table_name: string;
    value_column: string;
    date_column: string;
    aggregation: string;
  };
  plans: { store_code: string; period: string; amount: string }[];
  expected: {
    date_from: string;
    date_to: string;
    actual: string;
    plan: string;
    attainment: string;
    previous: string;
    change_percent: string;
  };
};
const fixture: Fixture | null = sourcePath
  ? JSON.parse(readFileSync(sourcePath, "utf8"))
  : null;

test("real PostgreSQL: catalog policy, metric, plans and reconciled financial overview", async ({
  page,
}, testInfo) => {
  test.setTimeout(75_000);
  if (!fixture) return;
  await page.goto(account.url);
  await page.getByLabel("Электронная почта").fill(account.email);
  await page.getByLabel("Пароль", { exact: true }).fill(account.password);
  await page.getByRole("button", { name: "Войти", exact: true }).click();
  await expect(page.getByRole("main")).toBeVisible();
  await expect(
    page.getByRole("link", { name: "Настройки", exact: true }),
  ).toBeVisible();
  const sessionResponse = await page.request.get(
    `${account.url}/api/v1/auth/session`,
  );
  const session = await sessionResponse.json();
  const send = async (path: string, body: unknown) => {
    const response = await page.request.post(`${account.url}/api/v1${path}`, {
      data: body,
      headers: { "X-CSRF-Token": session.csrf_token },
    });
    if (!response.ok())
      throw new Error(`Fixture API ${response.status()} ${path}`);
    return response.json();
  };
  const workspace = await send("/workspaces", {
    name: `Финансовая проверка ${Date.now()}`,
  });
  const prefix = `/workspaces/${workspace.id}`;
  for (const store of fixture.stores) await send(`${prefix}/stores`, store);
  const source = await send(`${prefix}/sources`, fixture.source);
  const checked = await send(`${prefix}/sources/${source.id}/test`, {});
  expect(checked.status).toBe("ready");
  await page.goto(`${account.url}/w/${workspace.id}/data`);
  await page.getByRole("button", { name: "Таблицы и доступ" }).click();
  const dialog = page.getByRole("dialog");
  for (const policy of fixture.policies.tables) {
    const table = dialog
      .locator(".policy-table")
      .filter({ hasText: `${policy.schema}.${policy.name}` });
    await table.locator(".policy-title input").check();
    for (const column of policy.columns)
      await table
        .getByRole("checkbox", { name: new RegExp(`^${column}(?:\\s|$)`) })
        .check();
    await table
      .getByLabel("Как ограничивается доступ к строкам")
      .selectOption(policy.shared ? "shared" : "store");
    if (policy.store_column)
      await table
        .getByLabel("Поле внешнего кода точки", { exact: false })
        .selectOption(policy.store_column);
  }
  await dialog.getByRole("button", { name: "Сохранить доступ" }).click();
  await expect(dialog).not.toBeVisible();
  await page.getByRole("button", { name: "Определения показателей" }).click();
  await page.getByRole("button", { name: "Добавить показатель" }).click();
  await dialog
    .getByLabel("Название", { exact: true })
    .fill(fixture.metric.name);
  await dialog
    .getByLabel("Постоянный ключ", { exact: false })
    .fill(fixture.metric.key);
  await dialog
    .getByLabel("Бизнес-смысл", { exact: false })
    .fill(fixture.metric.description);
  await dialog
    .getByRole("combobox", { name: "Источник", exact: true })
    .selectOption(source.id);
  await dialog
    .getByRole("combobox", { name: "Таблица фактов", exact: true })
    .selectOption(
      `${fixture.metric.table_schema}.${fixture.metric.table_name}`,
    );
  await dialog.getByLabel("Операция").selectOption(fixture.metric.aggregation);
  await dialog
    .getByLabel("Поле значения")
    .selectOption(fixture.metric.value_column);
  await dialog.getByLabel("Поле даты").selectOption(fixture.metric.date_column);
  await dialog.getByRole("button", { name: "Сохранить определение" }).click();
  await expect(dialog).not.toBeVisible();
  await page.getByRole("button", { name: "Планы точек" }).click();
  for (const plan of fixture.plans) {
    await page.getByRole("button", { name: "Задать план" }).click();
    await dialog
      .getByRole("combobox", { name: "Точка", exact: true })
      .selectOption({
        label: fixture.stores.find((store) => store.code === plan.store_code)!
          .name,
      });
    await dialog
      .getByRole("combobox", { name: "Показатель", exact: true })
      .selectOption({
        label: `${fixture.metric.name} · ${fixture.metric.unit} · v1`,
      });
    await dialog
      .getByLabel("Месяц", { exact: true })
      .fill(plan.period.slice(0, 7));
    await dialog
      .getByLabel("Значение", { exact: true })
      .fill(String(plan.amount));
    await dialog.getByRole("button", { name: "Сохранить план" }).click();
    await expect(dialog).not.toBeVisible();
  }
  await page.getByRole("link", { name: "Обзор", exact: true }).click();
  const overviewReady = page.waitForResponse(
    (response) =>
      response.url().includes("/overview?") &&
      response.url().includes(`date_from=${fixture.expected.date_from}`) &&
      response.status() === 200,
  );
  await page
    .getByLabel("Период", { exact: true })
    .fill(fixture.expected.date_from.slice(0, 7));
  const overviewResponse = await overviewReady;
  const overview = await overviewResponse.json();
  expect(overview.totals.actual).toBe(fixture.expected.actual);
  expect(overview.totals.plan).toBe(fixture.expected.plan);
  expect(overview.totals.attainment).toBe(fixture.expected.attainment);
  expect(overview.comparison.previous).toBe(fixture.expected.previous);
  const [year, month] = fixture.expected.date_from.split("-").map(Number);
  expect(overview.comparison.date_from).toBe(
    new Date(Date.UTC(year, month - 2, 1)).toISOString().slice(0, 10),
  );
  expect(overview.comparison.date_to).toBe(
    new Date(Date.UTC(year, month - 1, 0)).toISOString().slice(0, 10),
  );
  expect(overview.comparison.change_percent).toBe(
    fixture.expected.change_percent,
  );
  await expect(page.locator(".metric-card").first()).toContainText("700");
  await expect(page.locator(".comparison-change")).toHaveText("+40%");
  await expect(page.getByRole("table")).toContainText(fixture.stores[0].name);
  await page.evaluate(() => window.scrollTo(0, 0));
  await expect(page.locator(".sidebar .brand")).toBeInViewport();
  await page.getByRole("heading", { name: "Обзор показателей" }).click();
  await page.screenshot({
    path: testInfo.outputPath("analytics-viewport.png"),
  });
  await page.screenshot({
    path: testInfo.outputPath("analytics-desktop.png"),
    fullPage: true,
  });
  await page.setViewportSize({ width: 390, height: 844 });
  await expect
    .poll(() =>
      page.evaluate(
        () => document.documentElement.scrollWidth > window.innerWidth + 1,
      ),
    )
    .toBe(false);
  await page.screenshot({
    path: testInfo.outputPath("analytics-mobile.png"),
    fullPage: true,
  });
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page
    .getByRole("link", { name: fixture.stores[0].name, exact: false })
    .click();
  await expect(
    page.getByRole("heading", { name: fixture.stores[0].name, exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("heading", { name: "Динамика показателя" }),
  ).toBeVisible();
  await expect(page.getByRole("img", { name: /порядок из SQL/ })).toBeVisible();
  await page.getByRole("button", { name: "По неделям", exact: true }).click();
  await expect(page.getByRole("img", { name: /Начало недели/ })).toBeVisible();
  await page.getByRole("button", { name: "Таблица", exact: true }).click();
  await expect(page.getByRole("table")).toContainText("Строк источника");
  await page
    .getByRole("button", { name: "Создать разбор", exact: true })
    .click();
  await dialog
    .getByLabel("Название разбора")
    .fill("Проверка измерения после решения");
  await dialog
    .getByRole("button", { name: "Создать разбор", exact: true })
    .click();
  await expect(page.locator(".measurement-card")).toHaveCount(1);
  const original = overview.stores.find(
    (store: { name: string }) => store.name === fixture.stores[0].name,
  );
  await expect(page.locator(".measurement-values")).toContainText(
    original.actual.slice(0, 3),
  );
  await page.getByRole("button", { name: "Итог", exact: true }).click();
  await page
    .getByLabel("Что установили")
    .fill("Решение зафиксировано. Следующая проверка не меняет этот вывод.");
  await page.getByRole("button", { name: "Зафиксировать итог" }).click();
  await expect(page.locator(".conclusion")).toBeVisible();
  await page.getByRole("button", { name: "Повторить измерение" }).click();
  await dialog
    .getByLabel("Период измерения")
    .fill(overview.comparison.date_from.slice(0, 7));
  await dialog.getByRole("button", { name: "Зафиксировать измерение" }).click();
  await expect(page.locator(".measurement-card")).toHaveCount(2);
  await page.reload();
  await expect(page.locator(".measurement-card")).toHaveCount(2);
  await expect(page.locator(".conclusion")).toContainText(
    "Решение зафиксировано",
  );
  await expect(page.locator(".measurement-change")).toContainText(
    "исходному измерению",
  );
  await page.evaluate(() => window.scrollTo(0, 0));
  await page.screenshot({
    path: testInfo.outputPath("measurement-case.png"),
    fullPage: true,
  });
});
