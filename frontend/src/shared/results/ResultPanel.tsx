import { useMemo, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import type { ColumnDef } from "@tanstack/react-table";
import type { Run, Report, Case } from "../api/contracts";
import { post, workspacePath } from "../api/client";
import { keys, useSources, useStores } from "../api/queries";
import { useWorkspace } from "../../app/workspace";
import {
  Button,
  Field,
  Form,
  formText,
  InlineError,
  Modal,
  StatusBadge,
} from "../ui/Common";
import { compareDecimal, dateTime, decimal, periodLabel } from "../ui/format";
import { Icon } from "../ui/Icon";
import { useToast } from "../ui/Toast";
import { DataTable } from "./DataTable";
import { downloadText, rawCell, resultCsv } from "./export";
import { ResultChart } from "./ResultChart";
import { chartColumns } from "./chart";

function displayCell(value: unknown, type: string) {
  if (value === null || value === undefined)
    return (
      <span className="null-value" title="Значение отсутствует">
        —
      </span>
    );
  if (typeof value === "boolean") return value ? "Да" : "Нет";
  if (
    /numeric|decimal|int|float|double|real/i.test(type) &&
    ["number", "string"].includes(typeof value)
  )
    return <span className="numeric">{decimal(String(value), Infinity)}</span>;
  return <span className="result-cell-text">{rawCell(value)}</span>;
}

export function ResultPanel({
  run,
  onContinue,
  compact = false,
  reportId,
}: {
  run: Run;
  onContinue?: () => void;
  compact?: boolean;
  reportId?: string;
}) {
  const workspace = useWorkspace();
  const toast = useToast();
  const [saveKind, setSaveKind] = useState<"reports" | "cases" | null>(null);
  const [expanded, setExpanded] = useState(false);
  const [view, setView] = useState<"table" | "chart">("table");
  const sources = useSources(workspace.id);
  const stores = useStores(workspace.id);
  const chartOptions = run.result ? chartColumns(run.result) : null;
  const canChart = Boolean(
    run.result?.rows.length &&
    chartOptions?.measures.length &&
    chartOptions.dimensions.length,
  );
  const source = sources.data?.find((item) => item.id === run.source_id);
  const selectedStores =
    stores.data?.filter((item) => run.store_ids.includes(item.id)) ?? [];
  const columns = useMemo<ColumnDef<unknown[], unknown>[]>(
    () =>
      (run.result?.columns ?? []).map((column, index) => ({
        id: String(index),
        accessorFn: (row) => row[index],
        header: column.name || `Колонка ${index + 1}`,
        cell: (info) => displayCell(info.getValue(), column.type),
        sortingFn: /numeric|decimal|int|float|double|real/i.test(column.type)
          ? (a, b) => compareDecimal(a.original[index], b.original[index])
          : "alphanumeric",
      })),
    [run.result?.columns],
  );
  const copySql = async () => {
    try {
      await navigator.clipboard.writeText(run.sql ?? "");
      toast("SQL скопирован");
    } catch {
      toast("Копирование недоступно. Выделите текст SQL вручную.");
    }
  };
  const content = (
    <>
      <div className="result-heading">
        <div>
          <span className="result-label">
            <Icon name="reports" size={16} />
            Результат расчёта
          </span>
          <span className="result-time">
            {dateTime(run.finished_at ?? run.created_at)}
          </span>
        </div>
        <StatusBadge status={run.status} />
      </div>
      <p className="result-question">{run.question}</p>
      <div className="result-scope">
        <span>
          <Icon name="data" size={14} />
          {source?.name ?? "Источник расчёта"}
        </span>
        {selectedStores.length ? (
          selectedStores.slice(0, 4).map((store) => (
            <Link
              key={store.id}
              to={`/w/${workspace.id}/stores/${store.id}${run.context?.date_from ? `?month=${run.context.date_from.slice(0, 7)}` : ""}`}
            >
              {store.name}
            </Link>
          ))
        ) : (
          <span>Область: {run.store_ids.length} точек</span>
        )}
        {selectedStores.length > 4 && (
          <span>ещё {selectedStores.length - 4}</span>
        )}
      </div>
      {run.context && (
        <div className="result-context">
          <span>
            Запрошенный период:{" "}
            {periodLabel(run.context.date_from, run.context.date_to)}
          </span>
          {run.context.metric_version && (
            <span>Определение · версия {run.context.metric_version}</span>
          )}
        </div>
      )}
      {run.result ? (
        <>
          {canChart && (
            <div
              className="result-view-switch segmented"
              aria-label="Представление результата"
            >
              <button
                type="button"
                className={view === "table" ? "active" : ""}
                aria-pressed={view === "table"}
                onClick={() => setView("table")}
              >
                Таблица
              </button>
              <button
                type="button"
                className={view === "chart" ? "active" : ""}
                aria-pressed={view === "chart"}
                onClick={() => setView("chart")}
              >
                График
              </button>
            </div>
          )}
          {view === "chart" && canChart ? (
            <ResultChart result={run.result} />
          ) : (
            <DataTable
              rows={run.result.rows}
              columns={columns}
              caption="Результат SQL-запроса"
              pageSize={compact ? 10 : 20}
              empty="Запрос выполнен. Строк по этим условиям нет."
            />
          )}
          <div className="result-foot">
            <span>
              {run.result.rows.length} строк в сохранённом результате
              {run.result.truncated ? " · выдача ограничена" : ""}
            </span>
            {run.result.truncated && (
              <span className="text-warning">
                <Icon name="alert" size={14} />
                Это часть результата, не полный итог
              </span>
            )}
          </div>
        </>
      ) : null}
      {run.sql && (
        <details className="sql-details">
          <summary>
            <span>SQL и основание расчёта</span>
            <Icon name="down" size={15} />
          </summary>
          <div className="sql-toolbar">
            <span>Запрос, связанный именно с этим результатом</span>
            <Button variant="quiet" icon="copy" onClick={() => void copySql()}>
              Копировать
            </Button>
          </div>
          <pre tabIndex={0}>
            <code>{run.sql}</code>
          </pre>
          <p className="subtle-note">
            SQL позволяет проверить условия расчёта. Корректность
            бизнес-показателя зависит от согласованного определения.
          </p>
          {run.result?.execution && (
            <details className="execution-details">
              <summary>Выполненный запрос и ограничения доступа</summary>
              <p className="subtle-note">
                Этот запрос отправлен базе после применения разрешённой области
                точек. Параметры переданы отдельно от SQL.
              </p>
              <pre tabIndex={0}>
                <code>{run.result.execution.sql}</code>
              </pre>
              <div className="execution-parameters">
                <strong>Параметры выполнения</strong>
                <pre tabIndex={0}>
                  <code>
                    {JSON.stringify(run.result.execution.parameters, null, 2)}
                  </code>
                </pre>
              </div>
            </details>
          )}
        </details>
      )}
    </>
  );
  return (
    <section className="result-panel">
      {content}
      <div className="result-actions">
        {onContinue && (
          <Button variant="secondary" icon="assistant" onClick={onContinue}>
            Уточнить результат
          </Button>
        )}
        {workspace.can("reports:write") && run.result && (
          <Button
            variant="quiet"
            onClick={() => setSaveKind("reports")}
            icon="reports"
          >
            Сохранить отчёт
          </Button>
        )}
        {workspace.can("cases:write") && run.result && (
          <Button
            variant="quiet"
            onClick={() => setSaveKind("cases")}
            icon="cases"
          >
            {run.store_ids.length === 1
              ? "Создать разбор"
              : "Обсудить с аналитиком"}
          </Button>
        )}
        {run.result && (
          <Button
            variant="quiet"
            icon="download"
            onClick={() =>
              downloadText(
                resultCsv(run.result!),
                `razbor-${run.id.slice(0, 8)}${run.result!.truncated ? "-partial" : ""}.csv`,
                "text/csv;charset=utf-8",
              )
            }
          >
            {run.result.truncated ? "Скачать показанные строки" : "Скачать CSV"}
          </Button>
        )}
        <Button
          variant="quiet"
          icon="expand"
          onClick={() => setExpanded(true)}
          aria-label="Развернуть результат"
        />
      </div>
      <Modal
        open={expanded}
        onOpenChange={setExpanded}
        title="Результат расчёта"
        description={run.question}
        wide
      >
        <div className="expanded-result">{content}</div>
      </Modal>
      {saveKind && (
        <SaveResultDialog
          kind={saveKind}
          run={run}
          reportId={reportId}
          close={() => setSaveKind(null)}
        />
      )}
    </section>
  );
}

function SaveResultDialog({
  kind,
  run,
  close,
  reportId,
}: {
  kind: "reports" | "cases";
  run: Run;
  close: () => void;
  reportId?: string;
}) {
  const { id } = useWorkspace();
  const client = useQueryClient();
  const [saved, setSaved] = useState<Report | Case | null>(null);
  const mutation = useMutation({
    mutationFn: (body: Record<string, unknown>) =>
      post<Report | Case>(workspacePath(id, `/${kind}`), body),
    onSuccess: (result) => {
      setSaved(result);
      void client.invalidateQueries({ queryKey: keys.resource(id, kind) });
    },
  });
  return (
    <Modal
      open
      onOpenChange={(open) => !open && close()}
      title={
        kind === "reports"
          ? "Сохранить отчёт"
          : run.store_ids.length === 1
            ? "Создать разбор по точке"
            : "Обсудить результат"
      }
      description={
        kind === "reports"
          ? "Сохраняется конкретный результат. Повторный расчёт создаст новый запуск."
          : "Коллегам будет передан этот результат в его полной области. Получателем вопроса сможет стать только участник с доступом ко всем этим данным."
      }
    >
      <div className="modal-body">
        {saved ? (
          <div className="saved-confirmation">
            <Icon name="check" size={28} />
            <h3>{kind === "reports" ? "Отчёт сохранён" : "Разбор создан"}</h3>
            <p>{saved.title}</p>
            <Link
              onClick={close}
              className="button primary"
              to={`/w/${id}/${kind}/${saved.id}`}
            >
              Открыть {kind === "reports" ? "отчёт" : "разбор"}
            </Link>
          </div>
        ) : (
          <Form
            onSubmit={(form) =>
              mutation.mutate({
                title: formText(form, "title"),
                description: formText(form, "description"),
                run_id: run.id,
                ...(kind === "cases"
                  ? {
                      store_ids: run.store_ids,
                      ...(reportId ? { report_id: reportId } : {}),
                    }
                  : {}),
              })
            }
          >
            <Field label="Название">
              <input
                name="title"
                required
                maxLength={180}
                defaultValue={run.question.slice(0, 180)}
              />
            </Field>
            <Field label="Описание">
              <textarea
                name="description"
                rows={3}
                maxLength={3000}
                placeholder="Что важно учитывать при чтении этого результата?"
              />
            </Field>
            <InlineError error={mutation.error} />
            <div className="form-actions">
              <Button variant="secondary" onClick={close}>
                Отмена
              </Button>
              <Button type="submit" loading={mutation.isPending}>
                Сохранить
              </Button>
            </div>
          </Form>
        )}
      </div>
    </Modal>
  );
}
