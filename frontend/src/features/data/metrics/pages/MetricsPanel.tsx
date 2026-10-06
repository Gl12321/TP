import { useState } from "react";

import { useWorkspace } from "../../../../app/workspace";

import type { Metric } from "../../../../shared/api/contracts";
import { useMetrics } from "../../../../shared/api/queries";
import {
  Button,
  EmptyState,
  ErrorState,
  Loading,
  Panel,
} from "../../../../shared/ui/Common";

import { MetricDialog } from "../components/MetricDialog";
import { aggregationLabels } from "../model/aggregation";

export function MetricsPanel() {
  const workspace = useWorkspace();
  const query = useMetrics(workspace.id);
  const [editing, setEditing] = useState<Metric | "new" | null>(null);
  return (
    <>
      <div className="section-heading">
        <div>
          <h2>Что означают ваши показатели</h2>
          <p>
            Одно определение связывает расчёт на обзоре и вопрос ассистенту.
          </p>
        </div>
        {workspace.can("metrics:write") && (
          <Button icon="plus" onClick={() => setEditing("new")}>
            Добавить показатель
          </Button>
        )}
      </div>
      {query.isPending ? (
        <Loading />
      ) : query.error ? (
        <ErrorState error={query.error} retry={() => void query.refetch()} />
      ) : query.data?.length ? (
        <div className="definition-list">
          {query.data.map((metric) => (
            <Panel key={metric.id} className="definition-card">
              <div className="definition-top">
                <div>
                  <span className="eyebrow">
                    {metric.key} · версия {metric.version}
                  </span>
                  <h3>{metric.name}</h3>
                </div>
                <span className="unit-chip">{metric.unit}</span>
              </div>
              <p className="definition-description">{metric.description}</p>
              <dl className="metadata-list">
                <div>
                  <dt>Расчёт</dt>
                  <dd>
                    {aggregationLabels[metric.aggregation]}
                    {metric.value_column ? ` · ${metric.value_column}` : ""}
                  </dd>
                </div>
                <div>
                  <dt>Данные</dt>
                  <dd>
                    {metric.table_schema}.{metric.table_name}
                  </dd>
                </div>
                <div>
                  <dt>Дата</dt>
                  <dd>{metric.date_column}</dd>
                </div>
                <div>
                  <dt>Код точки</dt>
                  <dd>{metric.store_column}</dd>
                </div>
              </dl>
              {workspace.can("metrics:write") && (
                <Button variant="secondary" onClick={() => setEditing(metric)}>
                  Создать новую редакцию
                </Button>
              )}
            </Panel>
          ))}
        </div>
      ) : (
        <Panel>
          <EmptyState
            icon="reports"
            title="Сначала договоримся о значении чисел"
            description="Укажите таблицу, дату, поле суммы и бизнес-смысл показателя. Например, учитываются ли в выручке возвраты и скидки."
          />
        </Panel>
      )}
      {editing && (
        <MetricDialog
          metric={editing === "new" ? undefined : editing}
          close={() => setEditing(null)}
        />
      )}
    </>
  );
}
