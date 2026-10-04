import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useWorkspace } from "../../app/workspace";
import { get, queryString, workspacePath } from "../../shared/api/client";
import type { Result, StoreAnalytics } from "../../shared/api/contracts";
import { keys } from "../../shared/api/queries";
import { ErrorState, Loading, Panel } from "../../shared/ui/Common";
import { money } from "../../shared/ui/format";
import { ResultChart } from "../../shared/results/ResultChart";
import { DataTable } from "../../shared/results/DataTable";
import { CalculationDetails } from "../../shared/results/CalculationDetails";

export function StoreDynamics({
  storeId,
  metricId,
  dateFrom,
  dateTo,
}: {
  storeId: string;
  metricId: string;
  dateFrom: string;
  dateTo: string;
}) {
  const { id } = useWorkspace();
  const [grain, setGrain] = useState<"day" | "week">("day");
  const [view, setView] = useState<"chart" | "table">("chart");
  const query = useQuery({
    queryKey: keys.resource(
      id,
      "store-analytics",
      storeId,
      metricId,
      dateFrom,
      dateTo,
      grain,
    ),
    queryFn: ({ signal }) =>
      get<StoreAnalytics>(
        workspacePath(
          id,
          `/stores/${storeId}/analytics${queryString({ metric_id: metricId, date_from: dateFrom, date_to: dateTo, grain })}`,
        ),
        signal,
      ),
  });
  const result: Result | null = query.data
    ? {
        columns: [
          { name: grain === "day" ? "Дата" : "Начало недели", type: "date" },
          {
            name: `${query.data.metric.name}, ${query.data.metric.unit}`,
            type: "numeric",
          },
        ],
        rows: query.data.series.map((point) => [point.date, point.value]),
        row_count: query.data.series.length,
        truncated: false,
      }
    : null;
  return (
    <Panel
      title="Динамика показателя"
      description="Расчёт по тому же определению. Дни без строк в источнике остаются пропусками."
      className="spaced"
      actions={
        <div className="segmented">
          {[
            ["day", "По дням"],
            ["week", "По неделям"],
          ].map(([value, label]) => (
            <button
              type="button"
              key={value}
              className={grain === value ? "active" : ""}
              aria-pressed={grain === value}
              onClick={() => setGrain(value as "day" | "week")}
            >
              {label}
            </button>
          ))}
        </div>
      }
    >
      {query.error ? (
        <ErrorState error={query.error} retry={() => void query.refetch()} />
      ) : query.isPending ? (
        <Loading label="Рассчитываем динамику…" />
      ) : (
        result && (
          <>
            <div className="result-view-switch segmented">
              <button
                type="button"
                className={view === "chart" ? "active" : ""}
                aria-pressed={view === "chart"}
                onClick={() => setView("chart")}
              >
                График
              </button>
              <button
                type="button"
                className={view === "table" ? "active" : ""}
                aria-pressed={view === "table"}
                onClick={() => setView("table")}
              >
                Таблица
              </button>
            </div>
            {view === "chart" ? (
              <ResultChart key={`${grain}:${metricId}`} result={result} />
            ) : (
              <DataTable
                rows={query.data!.series}
                columns={[
                  {
                    accessorKey: "date",
                    header: grain === "day" ? "Дата" : "Начало недели",
                  },
                  {
                    accessorKey: "value",
                    header: query.data!.metric.name,
                    cell: (info) =>
                      money(info.row.original.value, query.data!.metric.unit),
                  },
                  { accessorKey: "weight", header: "Строк источника" },
                ]}
                caption="Динамика показателя точки"
              />
            )}
            {query.data!.warnings.map((warning, index) => (
              <p className="warning-note" key={index}>
                {warning}
              </p>
            ))}
            {query.data?.calculation && (
              <CalculationDetails calculation={query.data.calculation} />
            )}
          </>
        )
      )}
    </Panel>
  );
}
