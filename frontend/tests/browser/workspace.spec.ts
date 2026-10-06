import { readFileSync } from "node:fs";
import { test, expect } from "@playwright/test";

const credentialFile = process.env.APP_UI_CREDENTIALS;
test.skip(
  !credentialFile,
  "APP_UI_CREDENTIALS requires an isolated test account JSON",
);
const account: { url: string; email: string; password: string } = credentialFile
  ? JSON.parse(readFileSync(credentialFile, "utf8"))
  : { url: "", email: "", password: "" };

test("login, store, case, comment and logout through the real API", async ({
  page,
}) => {
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.goto(account.url);
  await page.getByLabel("Электронная почта").fill(account.email);
  await page.getByLabel("Пароль", { exact: true }).fill(account.password);
  await page.getByRole("button", { name: "Войти", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "Обзор показателей" }),
  ).toBeVisible();
  await page.getByRole("link", { name: "Точки", exact: true }).click();
  await page.getByRole("button", { name: "Добавить точку" }).click();
  const stamp = Date.now();
  const storeName = `UI ${stamp}`;
  const caseName = `Проверить данные ${stamp}`;
  await page.getByLabel("Название", { exact: true }).fill(storeName);
  await page.getByLabel("Внешний код", { exact: true }).fill(`UI-${stamp}`);
  await page.getByLabel("Город", { exact: true }).fill("Казань");
  await page.getByLabel("Партнёр", { exact: true }).fill("Тестовая команда");
  await page
    .getByRole("dialog")
    .getByRole("button", { name: "Добавить точку" })
    .click();
  await expect(page.getByRole("dialog")).not.toBeVisible();
  await page.getByRole("link", { name: storeName }).click();
  await expect(page.getByRole("heading", { name: storeName })).toBeVisible();
  await page.getByRole("button", { name: "Создать разбор" }).click();
  await page.getByLabel("Название разбора").fill(caseName);
  await page
    .getByLabel("Основание и контекст")
    .fill("Проверка связи обсуждения с точкой.");
  await page
    .getByRole("dialog")
    .getByRole("button", { name: "Создать разбор" })
    .click();
  await expect(page.getByRole("heading", { name: caseName })).toBeVisible();
  const message = "Данные проверены ответственным сотрудником.";
  await page.getByLabel("Сообщение", { exact: true }).fill(message);
  await page.getByRole("button", { name: "Добавить комментарий" }).click();
  await expect(
    page.locator(".comment").getByText(message, { exact: true }),
  ).toBeVisible();
  await page.getByRole("button", { name: "Выйти", exact: true }).click();
  await expect(page.getByLabel("Электронная почта")).toBeVisible();
  await page.reload();
  await expect(page.getByLabel("Электронная почта")).toBeVisible();
  expect(errors).toEqual([]);
});
