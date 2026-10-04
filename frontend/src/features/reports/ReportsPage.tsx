import { useEffect, useRef, useState } from "react";
import { Link, useParams, useSearchParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useWorkspace } from "../../app/workspace";
import { get, post, workspacePath } from "../../shared/api/client";
import type { Report, ReportHistory, Run } from "../../shared/api/contracts";
import { activeRun } from "../../shared/api/contracts";
import { keys } from "../../shared/api/queries";
import {
  Button,
  EmptyState,
  ErrorState,
  InlineError,
  Loading,
  PageHeader,
  Panel,
  StatusBadge,
} from "../../shared/ui/Common";
import { Icon } from "../../shared/ui/Icon";
import { dateTime } from "../../shared/ui/format";
import { RunCard } from "../assistant/RunCard";

export function ReportsPage() {
  const workspace = useWorkspace();
  const { reportId } = useParams();
  const [parameters, setParameters] = useSearchParams();
  const client = useQueryClient();
  const [search, setSearch] = useState("");
  const [refreshRun, setRefreshRun] = useState<Run | null>(null);
  const refreshKey = useRef("");
  const currentReport = useRef(reportId);
  currentReport.current = reportId;
  const reports = useQuery({
    queryKey: keys.resource(workspace.id, "reports"),
    queryFn: ({ signal }) =>
      get<Report[]>(workspacePath(workspace.id, "/reports"), signal),
  });
  const detail = useQuery({
    queryKey: keys.resource(workspace.id, "report", reportId),
    queryFn: ({ signal }) =>
      get<ReportHistory>(
        workspacePath(workspace.id, `/reports/${reportId}`),
        signal,
      ),
    enabled: Boolean(reportId),
    refetchInterval: (query) =>
      query.state.data?.refreshes?.some((run) => activeRun(run.status))
        ? 2000
        : false,
  });
  const refresh = useMutation({
    mutationFn: async () => {
      if (!refreshKey.current) refreshKey.current = crypto.randomUUID();
      const run = await post<Run>(
        workspacePath(workspace.id, `/reports/${reportId}/refresh`),
        { idempotency_key: refreshKey.current },
      );
      return { run, reportId };
    },
    onSuccess: ({ run, reportId: targetId }) => {
      if (currentReport.current === targetId) {
        setRefreshRun(run);
        setParameters({ run: run.id });
      }
      refreshKey.current = "";
      void client.invalidateQueries({
        queryKey: keys.resource(workspace.id, "reports"),
      });
      void client.invalidateQueries({
        queryKey: keys.resource(workspace.id, "report", targetId),
      });
    },
  });
  useEffect(() => {
    setRefreshRun(null);
    refreshKey.current = "";
  }, [reportId]);
  const history = detail.data
    ? [detail.data.run, ...(detail.data.refreshes ?? [])]
    : [];
  if (refreshRun && !history.some((run) => run.id === refreshRun.id))
    history.push(refreshRun);
  const selected =
    history.find((run) => run.id === parameters.get("run")) ?? history[0];
  const processing = history.some((run) => activeRun(run.status));
  if (reportId)
    return (
      <>
        <Link className="back-link" to={`/w/${workspace.id}/reports`}>
          <Icon name="back" size={16} />
          Все отчёты
        </Link>
        {detail.isPending ? (
          <Loading />
        ) : detail.error ? (
          <ErrorState
            error={detail.error}
            retry={() => void detail.refetch()}
          />
        ) : (
          detail.data && (
            <>
              <PageHeader
                eyebrow="Сохранённый расчёт"
                title={detail.data.title}
                description={
                  detail.data.description ||
                  "Готовый результат с зафиксированными условиями и запросом."
                }
                actions={
                  workspace.can("reports:write") && (
                    <Button
                      variant="secondary"
                      icon="refresh"
                      loading={refresh.isPending}
                      disabled={processing}
                      onClick={() => refresh.mutate()}
                    >
                      Пересчитать SQL
                    </Button>
                  )
                }
              />
              <div className="info-note">
                <Icon name="clock" size={18} />
                <span>
                  Сохранено {dateTime(detail.data.created_at)}. Открытие отчёта
                  не запускает модель и не обновляет исходные числа.
                </span>
              </div>
              <InlineError error={refresh.error} />
              <div className="report-history">
                <nav
                  className="report-history-list"
                  aria-label="История расчётов отчёта"
                >
                  <h2>История расчётов</h2>
                  {[...history].reverse().map((run, index) => (
                    <button
                      type="button"
                      key={run.id}
                      className={selected?.id === run.id ? "active" : ""}
                      aria-current={
                        selected?.id === run.id ? "true" : undefined
                      }
                      onClick={() => setParameters({ run: run.id })}
                    >
                      <strong>
                        {run.id === detail.data!.run.id
                          ? "Исходный результат"
                          : `Повторный расчёт ${history.length - index - 1}`}
                      </strong>
                      <span>{dateTime(run.created_at)}</span>
                      <StatusBadge status={run.status} />
                    </button>
                  ))}
                  {Boolean(detail.data.inaccessible_refreshes) && (
                    <p className="small muted">
                      Некоторые прежние расчёты закрыты текущими правами
                      доступа.
                    </p>
                  )}
                </nav>
                <div>
                  {selected && (
                    <RunCard
                      key={selected.id}
                      initial={selected}
                      reportId={reportId}
                    />
                  )}
                </div>
              </div>
            </>
          )
        )}
      </>
    );
  const filtered =
    reports.data?.filter((item) =>
      `${item.title} ${item.description}`
        .toLocaleLowerCase("ru")
        .includes(search.toLocaleLowerCase("ru")),
    ) ?? [];
  return (
    <>
      <PageHeader
        eyebrow="Повторяемая аналитика"
        title="Отчёты"
        description="Сохранённые таблицы и запросы. Возвращайтесь к результату или запускайте расчёт снова."
        actions={
          workspace.can("assistant:use") && (
            <Link
              className="button primary"
              to={`/w/${workspace.id}/assistant`}
            >
              <Icon name="plus" size={17} />
              Новый вопрос
            </Link>
          )
        }
      />
      <Panel>
        <div className="list-toolbar">
          <div className="search-input">
            <Icon name="search" size={17} />
            <input
              value={search}
              onChange={(event) => setSearch(event.target.value)}
              placeholder="Найти отчёт"
              aria-label="Найти отчёт"
            />
          </div>
          <span className="list-count">{filtered.length} отчётов</span>
        </div>
        {reports.isPending ? (
          <Loading />
        ) : reports.error ? (
          <ErrorState
            error={reports.error}
            retry={() => void reports.refetch()}
          />
        ) : filtered.length ? (
          <div className="report-grid">
            {filtered.map((item) => (
              <Link
                className="report-card"
                key={item.id}
                to={`/w/${workspace.id}/reports/${item.id}`}
              >
                <span className="report-icon">
                  <Icon name="reports" size={22} />
                </span>
                <h2>{item.title}</h2>
                <p>{item.description || "Сохранённый результат запроса"}</p>
                <footer>
                  <span>{dateTime(item.created_at)}</span>
                  <Icon name="arrow" size={18} />
                </footer>
              </Link>
            ))}
          </div>
        ) : (
          <EmptyState
            icon="reports"
            title={
              search
                ? "Отчёты не найдены"
                : "Первая полезная таблица — начало отчёта"
            }
            description={
              search
                ? "Измените поисковый запрос."
                : "Задайте вопрос ассистенту и сохраните полученную таблицу. Она появится здесь вместе с SQL и условиями."
            }
          />
        )}
      </Panel>
    </>
  );
}
