# Состояния обращения

[status.ts](status.ts) задаёт подписи для `open`, `in_progress` и `resolved`: «Открыто», «В работе» и «Решено».

Используйте `issueStatus` в [списке](../pages/SourceIssuesPanel.tsx) и [карточке обращения](../components/SourceIssueView.tsx). Допустимые переходы и требование решения при закрытии проверяет сервер.
