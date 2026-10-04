import { readFileSync } from "node:fs";
import { test, expect } from "@playwright/test";

const credentialFile = process.env.APP_UI_CREDENTIALS;
test.skip(
  !credentialFile,
  "APP_UI_CREDENTIALS must point to an isolated test account JSON",
);
const account: { url: string; email: string; password: string } = credentialFile
  ? JSON.parse(readFileSync(credentialFile, "utf8"))
  : { url: "", email: "", password: "" };

test("real API: login, directory, discussion, invitation and responsive navigation", async ({
  page,
  browser,
}, testInfo) => {
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.goto(account.url);
  await page.getByLabel("Электронная почта").fill(account.email);
  await page.getByLabel("Пароль", { exact: true }).fill(account.password);
  await page.getByRole("button", { name: "Войти", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "Обзор показателей" }),
  ).toBeVisible();
  await page.screenshot({
    path: testInfo.outputPath("overview-desktop.png"),
    fullPage: true,
  });
  await page.getByRole("link", { name: "Точки", exact: true }).click();
  await page.getByRole("button", { name: "Добавить точку" }).click();
  const stamp = Date.now();
  const storeName = `Проверка интерфейса ${stamp}`;
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
  await page.getByLabel("Название разбора").fill(`Проверить данные ${stamp}`);
  await page
    .getByLabel("Основание и контекст")
    .fill(
      "Проверка связи обсуждения с точкой. Финансовых значений в тесте нет.",
    );
  await page
    .getByRole("dialog")
    .getByRole("button", { name: "Создать разбор" })
    .click();
  await expect(
    page.getByRole("heading", { name: `Проверить данные ${stamp}` }),
  ).toBeVisible();
  await page
    .getByLabel("Сообщение", { exact: true })
    .fill("Данные проверены ответственным сотрудником.");
  await page.getByRole("button", { name: "Добавить комментарий" }).click();
  await expect(
    page
      .locator(".comment")
      .getByText("Данные проверены ответственным сотрудником.", {
        exact: true,
      }),
  ).toBeVisible();
  await page.screenshot({
    path: testInfo.outputPath("case-desktop.png"),
    fullPage: true,
  });
  await page.getByRole("link", { name: "Настройки", exact: true }).click();
  await page.getByRole("button", { name: "Команда", exact: true }).click();
  await page.getByRole("button", { name: "Добавить участника" }).click();
  await page.getByLabel("Почта сотрудника").fill(`ui-${stamp}@example.test`);
  await page
    .getByRole("dialog")
    .getByLabel(storeName, { exact: false })
    .check();
  await page.getByRole("button", { name: "Создать приглашение" }).click();
  await expect(
    page.getByRole("heading", { name: "Приглашение готово" }),
  ).toBeVisible();
  const invitation = await page
    .getByLabel("Личная ссылка приглашения")
    .inputValue();
  await page.getByRole("button", { name: "Готово", exact: true }).click();
  const invitedContext = await browser.newContext({
    viewport: { width: 1280, height: 900 },
  });
  const invited = await invitedContext.newPage();
  const invitationFailures: string[] = [];
  invited.on("response", (response) => {
    if (response.status() >= 400)
      invitationFailures.push(
        `${response.status()} ${new URL(response.url()).pathname}`,
      );
  });
  await invited.goto(invitation);
  await invited
    .getByLabel("Ваше имя", { exact: true })
    .fill("Управляющий тестовой точки");
  await invited
    .getByLabel("Придумайте пароль")
    .fill(`Test-only-password-${stamp}`);
  await invited
    .getByRole("button", { name: "Создать аккаунт и присоединиться" })
    .click();
  await expect(invited.getByRole("heading", { name: storeName }))
    .toBeVisible()
    .catch((error) => {
      throw new Error(
        `${String(error)}; HTTP: ${invitationFailures.join(", ")}`,
      );
    });
  await expect(
    invited.getByRole("link", { name: "Ассистент SQL" }),
  ).toBeVisible();
  await expect(
    invited.getByRole("button", { name: "Команда", exact: true }),
  ).not.toBeVisible();
  await page.getByRole("link", { name: "Разборы", exact: true }).click();
  await page
    .getByRole("link", { name: new RegExp(`Проверить данные ${stamp}`) })
    .click();
  await page
    .getByRole("button", { name: "Задать вопрос", exact: true })
    .click();
  await page
    .getByLabel("Получатель", { exact: false })
    .selectOption({ label: "Управляющий тестовой точки" });
  await page
    .getByLabel("Предметный вопрос", { exact: true })
    .fill("Подтвердите дату последнего обновления данных.");
  await page
    .getByRole("button", { name: "Задать вопрос", exact: true })
    .last()
    .click();
  await expect(page.locator(".addressed-question")).toBeVisible();
  await invited.getByRole("link", { name: "Разборы", exact: true }).click();
  await invited
    .getByRole("link", { name: new RegExp(`Проверить данные ${stamp}`) })
    .click();
  await invited
    .getByLabel("Ваш ответ", { exact: true })
    .fill("Обновление проверено сегодня, пропусков не обнаружено.");
  await invited.getByRole("button", { name: "Отправить ответ" }).click();
  await expect(
    invited
      .locator(".answer")
      .getByText("Обновление проверено сегодня, пропусков не обнаружено."),
  ).toBeVisible();
  await page.reload();
  await expect(page.locator(".answer")).toBeVisible();
  await page.getByRole("button", { name: "Итог", exact: true }).click();
  await page
    .getByLabel("Что установили")
    .fill("Ответ ответственного получен. Проверка завершена.");
  await page.getByRole("button", { name: "Зафиксировать итог" }).click();
  await expect(page.locator(".conclusion")).toBeVisible();
  await page.screenshot({
    path: testInfo.outputPath("case-desktop.png"),
    fullPage: true,
  });
  await invitedContext.close();
  await page.getByRole("link", { name: "Данные", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "Данные и показатели" }),
  ).toBeVisible();
  await page.screenshot({
    path: testInfo.outputPath("data-desktop.png"),
    fullPage: true,
  });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.getByRole("button", { name: "Открыть навигацию" }).click();
  await page.getByRole("link", { name: "Ассистент SQL" }).click();
  await expect(
    page.getByRole("heading", { name: "Ассистент", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "Получить таблицу" }),
  ).toBeDisabled();
  const overflow = await page.evaluate(
    () => document.documentElement.scrollWidth > window.innerWidth + 1,
  );
  expect(overflow).toBe(false);
  await page.screenshot({
    path: testInfo.outputPath("assistant-mobile.png"),
    fullPage: true,
  });
  expect(errors).toEqual([]);
});
