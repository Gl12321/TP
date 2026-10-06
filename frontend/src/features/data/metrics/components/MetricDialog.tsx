import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useWorkspace } from "../../../../app/workspace";
import { get, post, workspacePath } from "../../../../shared/api/client";
import type { CatalogTable, Metric } from "../../../../shared/api/contracts";
import { keys, useSources } from "../../../../shared/api/queries";
import {
  Button,
  Field,
  Form,
  formText,
  InlineError,
  Loading,
  Modal,
} from "../../../../shared/ui/Common";
import { Icon } from "../../../../shared/ui/Icon";
import { aggregationLabels } from "../model/aggregation";

export function MetricDialog({
  metric,
  close,
}: {
  metric?: Metric;
  close: () => void;
}) {
  const { id } = useWorkspace();
  const client = useQueryClient();
  const sources = useSources(id);
  const [sourceId, setSourceId] = useState(metric?.source_id ?? "");
  const [tableKey, setTableKey] = useState(
    metric ? `${metric.table_schema}.${metric.table_name}` : "",
  );
  const [aggregation, setAggregation] = useState<Metric["aggregation"]>(
    metric?.aggregation ?? "sum",
  );
  const catalog = useQuery({
    queryKey: keys.resource(id, "catalog", sourceId),
    queryFn: ({ signal }) =>
      get<CatalogTable[]>(
        workspacePath(id, `/sources/${sourceId}/catalog`),
        signal,
      ),
    enabled: Boolean(sourceId),
  });
  const tables =
    catalog.data?.filter(
      (table) => table.policy?.store_column && !table.policy.shared,
    ) ?? [];
  const selected = tables.find(
    (table) => `${table.schema}.${table.name}` === tableKey,
  );
  const columns =
    selected?.columns.filter((column) =>
      selected.policy?.columns.includes(column.name),
    ) ?? [];
  const mutation = useMutation({
    mutationFn: (body: object) => post(workspacePath(id, "/metrics"), body),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: keys.workspace(id) });
      close();
    },
  });
  return (
    <Modal
      open
      onOpenChange={(open) => !open && close()}
      title={
        metric ? `Новая редакция · ${metric.name}` : "Определить показатель"
      }
      description="Описание поясняет смысл. Числа рассчитываются выбранной операцией по разрешённой таблице; исключения и возвраты должны быть подготовлены в данных источника."
    >
      <div className="modal-body">
        <Form
          onSubmit={(form) => {
            if (!selected?.policy?.store_column) return;
            mutation.mutate({
              key: metric?.key ?? formText(form, "key"),
              name: formText(form, "name"),
              description: formText(form, "description"),
              unit: formText(form, "unit"),
              source_id: sourceId,
              table_schema: selected.schema,
              table_name: selected.name,
              aggregation,
              value_column:
                aggregation === "count" ? null : formText(form, "value_column"),
              date_column: formText(form, "date_column"),
              store_column: selected.policy.store_column,
            });
          }}
        >
          <div className="form-grid">
            <Field label="Название">
              <input
                name="name"
                required
                maxLength={160}
                defaultValue={metric?.name}
                placeholder="Чистая выручка"
              />
            </Field>
            <Field
              label="Постоянный ключ"
              hint="Латиница, цифры и подчёркивание."
            >
              <input
                name="key"
                required
                pattern="[a-z][a-z0-9_]*"
                maxLength={80}
                defaultValue={metric?.key}
                readOnly={Boolean(metric)}
                placeholder="net_revenue"
              />
            </Field>
          </div>
          <Field
            label="Бизнес-смысл"
            hint="Укажите, что учитывается, какая дата используется и какие исключения подготовлены в источнике."
          >
            <textarea
              name="description"
              rows={4}
              required
              minLength={10}
              maxLength={4000}
              defaultValue={metric?.description}
              placeholder="Например: сумма оплаченных продаж за вычетом возвратов, по дате операции. Значения уже подготовлены в поле net_amount."
            />
          </Field>
          <Field label="Источник">
            <select
              required
              value={sourceId}
              onChange={(event) => {
                setSourceId(event.target.value);
                setTableKey("");
              }}
            >
              <option value="">Выберите источник</option>
              {sources.data
                ?.filter(
                  (source) => source.enabled && source.status === "ready",
                )
                .map((source) => (
                  <option key={source.id} value={source.id}>
                    {source.name}
                  </option>
                ))}
            </select>
          </Field>
          {catalog.isFetching && (
            <Loading label="Открываем разрешённые таблицы…" />
          )}
          <Field label="Таблица фактов">
            <select
              required
              value={tableKey}
              onChange={(event) => setTableKey(event.target.value)}
              disabled={!sourceId || catalog.isPending}
            >
              <option value="">Выберите таблицу</option>
              {tables.map((table) => (
                <option
                  key={`${table.schema}.${table.name}`}
                  value={`${table.schema}.${table.name}`}
                >
                  {table.schema}.{table.name}
                </option>
              ))}
            </select>
          </Field>
          {sourceId &&
            !catalog.isPending &&
            !catalog.error &&
            !tables.length && (
              <p className="warning-note">
                Нет разрешённых таблиц с кодом точки. Администратор должен
                настроить доступ к данным.
              </p>
            )}
          <div className="form-grid">
            <Field label="Операция">
              <select
                value={aggregation}
                onChange={(event) =>
                  setAggregation(event.target.value as Metric["aggregation"])
                }
              >
                {Object.entries(aggregationLabels).map(([value, label]) => (
                  <option value={value} key={value}>
                    {label}
                  </option>
                ))}
              </select>
            </Field>
            <Field label="Единица измерения">
              <input
                name="unit"
                required
                maxLength={24}
                defaultValue={metric?.unit ?? "RUB"}
                list="metric-units"
              />
              <datalist id="metric-units">
                <option value="RUB" />
                <option value="шт." />
                <option value="заказов" />
              </datalist>
            </Field>
          </div>
          <div key={tableKey} className="form-grid">
            {aggregation !== "count" && (
              <Field label="Поле значения">
                <select
                  name="value_column"
                  required
                  defaultValue={metric?.value_column ?? ""}
                >
                  <option value="">Выберите поле</option>
                  {columns.map((column) => (
                    <option value={column.name} key={column.name}>
                      {column.name} · {column.data_type}
                    </option>
                  ))}
                </select>
              </Field>
            )}
            <Field label="Поле даты">
              <select
                name="date_column"
                required
                defaultValue={metric?.date_column ?? ""}
              >
                <option value="">Выберите поле</option>
                {columns.map((column) => (
                  <option value={column.name} key={column.name}>
                    {column.name} · {column.data_type}
                  </option>
                ))}
              </select>
            </Field>
          </div>
          {selected && (
            <p className="info-note">
              <Icon name="lock" size={16} />
              Область точек ограничивается полем {selected.policy?.store_column}
              .
            </p>
          )}
          {metric && (
            <p className="subtle-note">
              Предыдущие расчёты сохранят своё определение. Для новой редакции
              плана используются новые значения отдельно.
            </p>
          )}
          <InlineError
            error={mutation.error ?? catalog.error ?? sources.error}
          />
          <div className="form-actions">
            <Button variant="secondary" onClick={close}>
              Отмена
            </Button>
            <Button
              type="submit"
              loading={mutation.isPending}
              disabled={!selected}
            >
              Сохранить определение
            </Button>
          </div>
        </Form>
      </div>
    </Modal>
  );
}
