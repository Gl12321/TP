export function caseStatus(status: string) {
  return (
    (
      {
        open: "Открыт",
        draft: "Сохранён",
        waiting: "Ждём ответа",
        answered: "Ответ получен",
        closed: "Итог зафиксирован",
        done: "Итог зафиксирован",
      } as Record<string, string>
    )[status] ?? status
  );
}
