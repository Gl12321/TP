import { useEffect, useRef, useState } from "react";
import { Link, useParams, useSearchParams } from "react-router-dom";
import {
  useInfiniteQuery,
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";
import { useWorkspace } from "../../../app/workspace";
import {
  get,
  getPage,
  post,
  queryString,
  workspacePath,
} from "../../../shared/api/client";
import type { Report, ReportHistory, Run } from "../../../shared/api/contracts";
import { activeRun } from "../../../shared/api/contracts";
import { keys } from "../../../shared/api/queries";
import {
  Button,
  EmptyState,
  ErrorState,
  InlineError,
  Loading,
  PageHeader,
  Panel,
  StatusBadge,
} from "../../../shared/ui/Common";
import { Icon } from "../../../shared/ui/Icon";
import { dateTime } from "../../../shared/ui/format";
import { useDebouncedValue } from "../../../shared/ui/useDebouncedValue";
import { RunCard } from "../../assistant/components/RunCard";

export function ReportsPage() {
  const workspace = useWorkspace();
  const { reportId } = useParams();
  const [parameters, setParameters] = useSearchParams();
  const client = useQueryClient();
  const search = (parameters.get("q") ?? "").slice(0, 200);
  const searchQuery = useDebouncedValue(search.trim());
  const listParameters = new URLSearchParams(parameters);
  listParameters.delete("run");
  const listSearch = listParameters.size ? `?${listParameters.toString()}` : "";
  const [refreshRun, setRefreshRun] = useState<Run | null>(null);
  const refreshKey = useRef("");
  const currentReport = useRef(reportId);
  currentReport.current = reportId;
  const reports = useInfiniteQuery({
    queryKey: keys.resource(workspace.id, "reports", "infinite", searchQuery),
    queryFn: ({ signal, pageParam }) =>
      getPage<Report>(
        workspacePath(
          workspace.id,
          `/reports${queryString({ q: searchQuery, cursor: pageParam, limit: 50 })}`,
        ),
        signal,
      ),
    initialPageParam: null as string | null,
    getNextPageParam: (last) => last.nextCursor,
    enabled: !reportId,
  });
  const reportItems = [
    ...new Map(
      (reports.data?.pages.flatMap((page) => page.items) ?? []).map((item) => [
        item.id,
        item,
      ]),
    ).values(),
  ];
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
        setParameters((previous) => {
          const next = new URLSearchParams(previous);
          next.set("run", run.id);
          return next;
        });
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
        <Link
          className="back-link"
          to={`/w/${workspace.id}/reports${listSearch}`}
        >
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
                      onClick={() =>
                        setParameters((previous) => {
                          const next = new URLSearchParams(previous);
                          next.set("run", run.id);
                          return next;
                        })
                      }
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
              type="search"
              value={search}
              onChange={(event) => {
                const value = event.target.value;
                setParameters(
                  (previous) => {
                    const next = new URLSearchParams(previous);
                    if (value) next.set("q", value);
                    else next.delete("q");
                    next.delete("run");
                    return next;
                  },
                  { replace: true },
                );
              }}
              placeholder="Найти отчёт"
              aria-label="Найти отчёт"
              maxLength={200}
            />
          </div>
          <span className="list-count" role="status">
            {reports.data ? `Загружено: ${reportItems.length}` : ""}
          </span>
        </div>
        {reports.isPending ? (
          <Loading />
        ) : reports.error && !reports.data ? (
          <ErrorState
            error={reports.error}
            retry={() => void reports.refetch()}
          />
        ) : reportItems.length ? (
          <div className="report-grid">
            {reportItems.map((item) => (
              <Link
                className="report-card"
                key={item.id}
                to={`/w/${workspace.id}/reports/${item.id}${listSearch}`}
              >
                <span className="report-icon">
                  <Icon name="reports" size={22} />
                </span>
                <h2>{item.title}</h2>
                <p>{item.description || "Сохранённый результат запроса"}</p>
                <footer>
                  <time dateTime={item.created_at}>
                    {dateTime(item.created_at)}
                  </time>
                  <Icon name="arrow" size={18} />
                </footer>
              </Link>
            ))}
          </div>
        ) : (
          <EmptyState
            icon="reports"
            title={
              reports.hasNextPage
                ? "В загруженной части списка нет отчётов"
                : searchQuery
                  ? "Отчёты не найдены"
                  : "Первая полезная таблица — начало отчёта"
            }
            description={
              reports.hasNextPage
                ? "Загрузите следующую страницу, чтобы проверить более ранние доступные отчёты."
                : searchQuery
                  ? "Измените поисковый запрос."
                  : "Задайте вопрос ассистенту и сохраните полученную таблицу. Она появится здесь вместе с SQL и условиями."
            }
          />
        )}
        {reports.error && reports.data && (
          <ErrorState
            error={reports.error}
            retry={() =>
              void (reports.isFetchNextPageError
                ? reports.fetchNextPage()
                : reports.refetch())
            }
          />
        )}
        {reports.hasNextPage && !reports.error && (
          <div className="list-toolbar">
            <Button
              variant="secondary"
              loading={reports.isFetchingNextPage}
              disabled={reports.isFetching}
              onClick={() => void reports.fetchNextPage()}
            >
              Показать ещё
            </Button>
          </div>
        )}
      </Panel>
    </>
  );
}
