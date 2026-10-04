import { useMutation, useQueryClient } from "@tanstack/react-query";
import { post, workspacePath } from "../../shared/api/client";
import type { Run } from "../../shared/api/contracts";
import { activeRun } from "../../shared/api/contracts";
import { keys } from "../../shared/api/queries";
import { useWorkspace } from "../../app/workspace";
import {
  Button,
  ErrorState,
  InlineError,
  StatusBadge,
} from "../../shared/ui/Common";
import { Icon } from "../../shared/ui/Icon";
import { periodLabel } from "../../shared/ui/format";
import { ResultPanel } from "../../shared/results/ResultPanel";
import { useRun } from "./useRun";

export function RunCard({
  initial,
  onContinue,
  reportId,
}: {
  initial: Run;
  onContinue?: (run: Run) => void;
  reportId?: string;
}) {
  const { id } = useWorkspace();
  const client = useQueryClient();
  const query = useRun(id, initial.id, initial);
  const run = query.data ?? initial;
  const cancel = useMutation({
    mutationFn: () => post<Run>(workspacePath(id, `/runs/${run.id}/cancel`)),
    onSuccess: (result) => {
      client.setQueryData(keys.resource(id, "run", run.id), result);
      void client.invalidateQueries({
        queryKey: keys.resource(id, "conversation"),
      });
    },
  });
  if (query.error)
    return (
      <ErrorState error={query.error} retry={() => void query.refetch()} />
    );
  if (run.status === "succeeded" && run.result)
    return (
      <ResultPanel
        run={run}
        reportId={reportId}
        onContinue={onContinue ? () => onContinue(run) : undefined}
      />
    );
  return (
    <div className={`run-state ${activeRun(run.status) ? "working" : ""}`}>
      <div className="run-state-heading">
        {activeRun(run.status) ? (
          <span className="spinner small" />
        ) : (
          <Icon
            name={
              run.status === "needs_input"
                ? "assistant"
                : run.status === "cancelled"
                  ? "stop"
                  : "alert"
            }
            size={19}
          />
        )}
        <StatusBadge status={run.status} />
      </div>
      <p>
        {run.status === "queued"
          ? "Задание сохранено и ожидает свободного исполнителя. Можно продолжать работу в других разделах."
          : run.status === "cancel_requested"
            ? "Ожидаем остановки вычисления. Ресурс освободится после подтверждения сервера."
            : run.status === "running"
              ? run.stage || "Формируем и проверяем запрос к данным…"
              : run.status === "cancelled"
                ? "Вычисление остановлено. Прежние результаты разговора сохранены."
                : (run.clarification?.message ??
                  run.error?.message ??
                  "Расчёт не завершён.")}
      </p>
      {run.context?.date_from && (
        <span className="small muted">
          Запрошено: {periodLabel(run.context.date_from, run.context.date_to)}
        </span>
      )}
      {query.connection === "reconnecting" && activeRun(run.status) && (
        <p className="small muted">
          Восстанавливаем поток событий. Состояние также проверяется через
          сервер.
        </p>
      )}
      <div className="inline-actions">
        {activeRun(run.status) && (
          <Button
            variant="secondary"
            icon="stop"
            loading={cancel.isPending}
            disabled={run.status === "cancel_requested"}
            onClick={() => cancel.mutate()}
          >
            Остановить
          </Button>
        )}
        {onContinue && !activeRun(run.status) && (
          <Button variant="secondary" onClick={() => onContinue(run)}>
            {run.status === "needs_input"
              ? "Ответить на уточнение"
              : "Исправить вопрос"}
          </Button>
        )}
      </div>
      <InlineError error={cancel.error ?? query.error} />
      {run.sql && (
        <details className="failed-sql">
          <summary>Последний SQL этой попытки</summary>
          <pre>
            <code>{run.sql}</code>
          </pre>
          <p className="small muted">
            Успешный результат этим запросом не подтверждён.
          </p>
        </details>
      )}
    </div>
  );
}
