import { useMemo } from "react";
import { Link, useSearchParams } from "react-router-dom";

import type { ColumnDef } from "@tanstack/react-table";
import { useFilters, useWorkspace } from "../../../app/workspace";
import type { OverviewRow, CityOverview } from "../../../shared/api/contracts";

import {
  ErrorState,
  Loading,
  PageHeader,
  Panel,
  StatusBadge,
  Button,
} from "../../../shared/ui/Common";
import { compareDecimal, money, percent } from "../../../shared/ui/format";
import { Icon } from "../../../shared/ui/Icon";
import { DataTable } from "../../../shared/results/DataTable";
import { ScopeFilters } from "../components/ScopeFilters";
import { PeriodComparison } from "../components/PeriodComparison";
import { WorkspaceSetup } from "../../onboarding/components/WorkspaceSetup";
import { useOverview } from "../hooks/useOverview";
import { MetricCard } from "../components/MetricCard";
import { Attainment } from "../components/Attainment";

export function OverviewPage() {
  const workspace = useWorkspace();
  const filters = useFilters();
  const query = useOverview();
  const [parameters] = useSearchParams();
  const grouping = parameters.get("group") === "cities" ? "cities" : "stores";
  const unit = query.data?.metric?.unit ?? "";
  const columns = useMemo<ColumnDef<OverviewRow, unknown>[]>(
    () => [
      {
        accessorKey: "name",
        header: "Точка",
        cell: (info) => (
          <Link
            className="table-link"
            to={`/w/${workspace.id}/stores/${info.row.original.store_id}?month=${filters.month}&metric=${query.metricId}`}
          >
            {info.row.original.name}
            <span>{info.row.original.city}</span>
          </Link>
        ),
      },
      {
        accessorKey: "actual",
        header: "Факт",
        cell: (info) => (
          <strong className="numeric">
            {money(info.row.original.actual, unit)}
          </strong>
        ),
        sortingFn: (a, b) =>
          compareDecimal(a.original.actual, b.original.actual),
      },
      {
        accessorKey: "plan",
        header: "План",
        cell: (info) => (
          <span className="numeric">{money(info.row.original.plan, unit)}</span>
        ),
        sortingFn: (a, b) => compareDecimal(a.original.plan, b.original.plan),
      },
      {
        accessorKey: "attainment",
        header: "Выполнение",
        cell: (info) => <Attainment value={info.row.original.attainment} />,
        sortingFn: (a, b) =>
          compareDecimal(a.original.attainment, b.original.attainment),
      },
      {
        accessorKey: "change_percent",
        header: "Изменение к прошлому периоду",
        cell: (info) => percent(info.row.original.change_percent),
        sortingFn: (a, b) =>
          compareDecimal(a.original.change_percent, b.original.change_percent),
      },
      {
        accessorKey: "status",
        header: "Данные",
        cell: (info) => (
          <StatusBadge status={info.row.original.status}>
            {info.row.original.actual === null ? "Нет данных" : "Есть данные"}
          </StatusBadge>
        ),
      },
    ],
    [workspace.id, filters.month, query.metricId, unit],
  );
  const cityColumns: ColumnDef<CityOverview, unknown>[] = [
    {
      accessorKey: "city",
      header: "Город",
      cell: (info) => (
        <Button
          variant="quiet"
          onClick={() =>
            filters.set({
              stores:
                query.data?.stores
                  .filter(
                    (store) =>
                      (store.city || "Без города") === info.row.original.city,
                  )
                  .map((store) => store.store_id)
                  .join(",") ?? null,
              group: null,
            })
          }
        >
          {info.row.original.city}
        </Button>
      ),
    },
    {
      accessorKey: "actual",
      header: "Факт",
      cell: (info) => money(info.row.original.actual, unit),
      sortingFn: (a, b) => compareDecimal(a.original.actual, b.original.actual),
    },
    {
      accessorKey: "plan",
      header: "План",
      cell: (info) => money(info.row.original.plan, unit),
      sortingFn: (a, b) => compareDecimal(a.original.plan, b.original.plan),
    },
    {
      accessorKey: "attainment",
      header: "Выполнение",
      cell: (info) => percent(info.row.original.attainment),
      sortingFn: (a, b) =>
        compareDecimal(a.original.attainment, b.original.attainment),
    },
    {
      accessorKey: "change_percent",
      header: "Изменение сопоставимых точек",
      cell: (info) => (
        <span>
          {percent(info.row.original.change_percent)}
          <small className="table-cell-subtitle">
            По {info.row.original.comparable_count} точкам
          </small>
        </span>
      ),
      sortingFn: (a, b) =>
        compareDecimal(a.original.change_percent, b.original.change_percent),
    },
    {
      id: "coverage",
      header: "Есть данные",
      cell: (info) =>
        `${info.row.original.available_count} из ${info.row.original.store_count} точек`,
    },
  ];
  return (
    <>
      <PageHeader
        eyebrow={
          workspace.all_stores
            ? "Рабочее пространство сети"
            : "Ваша область ответственности"
        }
        title="Обзор показателей"
        description="Сравнивайте результаты и планы по доступным точкам. Область расчёта и ограничения видны рядом с числами."
      />
      {Boolean(query.metrics.data?.length) && <ScopeFilters />}
      {query.metrics.error ? (
        <ErrorState
          error={query.metrics.error}
          retry={() => void query.metrics.refetch()}
        />
      ) : query.metrics.isPending ? (
        <Loading />
      ) : !query.metrics.data?.length ? (
        <WorkspaceSetup />
      ) : query.error ? (
        <ErrorState error={query.error} retry={() => void query.refetch()} />
      ) : query.isPending ? (
        <Loading label="Рассчитываем показатели…" />
      ) : (
        query.data && (
          <>
            <div className="metric-cards">
              <MetricCard
                label={query.data.metric?.name ?? "Факт"}
                value={money(query.data.totals.actual, unit)}
                foot={`Есть данные по ${query.data.coverage.available} из ${query.data.coverage.total} точек`}
                icon="overview"
              />
              <MetricCard
                label="План на выбранный период"
                value={money(query.data.totals.plan, unit)}
                foot="По точкам и планам в выбранной области"
                icon="reports"
              />
              <MetricCard
                label="Выполнение плана"
                value={percent(query.data.totals.attainment)}
                foot="По точкам, где есть и факт, и план"
                icon="stores"
              />
            </div>
            {query.data.comparison && (
              <PeriodComparison
                comparison={query.data.comparison}
                unit={unit}
              />
            )}
            <div
              className={`coverage-banner ${query.data.coverage.available < query.data.coverage.total ? "incomplete" : ""}`}
            >
              <Icon
                name={
                  query.data.coverage.available < query.data.coverage.total
                    ? "alert"
                    : "check"
                }
                size={19}
              />
              <div>
                <strong>
                  {query.data.coverage.available === query.data.coverage.total
                    ? "Данные доступны по всем выбранным точкам"
                    : "Общая картина пока неполная"}
                </strong>
                <span>
                  {query.data.coverage.available} из {query.data.coverage.total}{" "}
                  точек · отсутствие данных не считается нулём
                </span>
              </div>
              <Link to={`/w/${workspace.id}/stores?month=${filters.month}`}>
                Посмотреть точки
                <Icon name="arrow" size={15} />
              </Link>
            </div>
            {query.data.warnings.map((warning, index) => (
              <p className="warning-note" key={index}>
                <Icon name="alert" size={16} />
                {warning}
              </p>
            ))}
            <Panel
              title={
                grouping === "cities"
                  ? "Показатели городов"
                  : "Показатели точек"
              }
              actions={
                query.data.cities?.length ? (
                  <div className="segmented" aria-label="Группировка обзора">
                    <button
                      type="button"
                      className={grouping === "stores" ? "active" : ""}
                      aria-pressed={grouping === "stores"}
                      onClick={() => filters.set({ group: null })}
                    >
                      Точки
                    </button>
                    <button
                      type="button"
                      className={grouping === "cities" ? "active" : ""}
                      aria-pressed={grouping === "cities"}
                      onClick={() => filters.set({ group: "cities" })}
                    >
                      Города
                    </button>
                  </div>
                ) : undefined
              }
              description={
                query.data.metric?.description ||
                "Откройте точку, чтобы перейти к её данным и обсуждениям."
              }
            >
              {grouping === "cities" && query.data.cities ? (
                <DataTable
                  rows={query.data.cities}
                  columns={cityColumns}
                  caption="Показатели городов"
                />
              ) : (
                <DataTable
                  rows={query.data.stores}
                  columns={columns}
                  caption="Показатели доступных точек"
                  empty="В выбранной области пока нет точек."
                />
              )}
              <div className="panel-foot">
                <Icon name="lock" size={14} />
                <span>
                  Факт и план относятся к выбранному периоду и показателю.
                </span>
              </div>
            </Panel>
            {workspace.can("assistant:use") && (
              <section className="ask-banner">
                <div className="ask-icon">
                  <Icon name="assistant" size={25} />
                </div>
                <div>
                  <h2>Нужен другой срез?</h2>
                  <p>
                    Опишите нужную таблицу своими словами. У результата будут
                    собственные условия и SQL.
                  </p>
                </div>
                <Link
                  className="button primary"
                  to={`/w/${workspace.id}/assistant?month=${filters.month}${filters.storeIds.length ? `&stores=${filters.storeIds.join(",")}` : ""}`}
                >
                  Спросить по данным
                  <Icon name="arrow" size={16} />
                </Link>
              </section>
            )}
          </>
        )
      )}
    </>
  );
}
