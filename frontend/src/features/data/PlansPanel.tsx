import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useWorkspace } from "../../app/workspace";
import { get, post, workspacePath } from "../../shared/api/client";
import type { Plan } from "../../shared/api/contracts";
import { keys, useMetrics, useStores } from "../../shared/api/queries";
import {
  Button,
  EmptyState,
  ErrorState,
  Field,
  Form,
  formText,
  InlineError,
  Loading,
  Modal,
  Panel,
} from "../../shared/ui/Common";
import { money, previousMonth } from "../../shared/ui/format";

export function PlansPanel() {
  const workspace = useWorkspace();
  const client = useQueryClient();
  const stores = useStores(workspace.id);
  const metrics = useMetrics(workspace.id);
  const query = useQuery({
    queryKey: keys.resource(workspace.id, "plans"),
    queryFn: ({ signal }) =>
      get<Plan[]>(workspacePath(workspace.id, "/plans"), signal),
  });
  const [month, setMonth] = useState(previousMonth());
  const [editing, setEditing] = useState<Plan | "new" | null>(null);
  const mutation = useMutation({
    mutationFn: (body: object) =>
      post(workspacePath(workspace.id, "/plans"), body),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: keys.workspace(workspace.id) });
      setEditing(null);
    },
  });
  const items =
    query.data?.filter((plan) => plan.period.startsWith(month)) ?? [];
  const current = editing && editing !== "new" ? editing : undefined;
  const error = query.error ?? stores.error ?? metrics.error;
  return (
    <>
      <div className="section-heading">
        <div>
          <h2>Планы по точкам</h2>
          <p>
            Согласованные значения на месяц. Новое сохранение создаёт редакцию
            плана.
          </p>
        </div>
        {workspace.can("plans:write") && (
          <Button
            icon="plus"
            disabled={!stores.data?.length || !metrics.data?.length}
            onClick={() => {
              mutation.reset();
              setEditing("new");
            }}
          >
            Задать план
          </Button>
        )}
      </div>
      <Panel>
        <div className="list-toolbar">
          <label className="filter-field">
            Месяц
            <input
              type="month"
              value={month}
              onChange={(event) =>
                event.target.value && setMonth(event.target.value)
              }
            />
          </label>
          <span className="list-count">{items.length} планов</span>
        </div>
        {error ? (
          <ErrorState
            error={error}
            retry={() => {
              void query.refetch();
              void stores.refetch();
              void metrics.refetch();
            }}
          />
        ) : query.isPending || stores.isPending || metrics.isPending ? (
          <Loading />
        ) : items.length ? (
          <div
            className="table-scroll"
            role="region"
            aria-label="Планы точек"
            tabIndex={0}
          >
            <table className="data-table">
              <caption className="sr-only">Планы за {month}</caption>
              <thead>
                <tr>
                  <th>Точка</th>
                  <th>Показатель</th>
                  <th>План</th>
                  <th>Редакция</th>
                  <th>
                    <span className="sr-only">Действия</span>
                  </th>
                </tr>
              </thead>
              <tbody>
                {items.map((plan) => {
                  const metric = metrics.data?.find(
                    (item) => item.id === plan.metric_id,
                  );
                  return (
                    <tr key={plan.id}>
                      <td>
                        <strong>
                          {stores.data?.find(
                            (store) => store.id === plan.store_id,
                          )?.name ?? "Точка недоступна"}
                        </strong>
                      </td>
                      <td>
                        {metric?.name ?? "Предыдущая редакция показателя"}
                      </td>
                      <td className="numeric">
                        {money(plan.amount, metric?.unit ?? "")}
                      </td>
                      <td>v{plan.version}</td>
                      <td>
                        {workspace.can("plans:write") && (
                          <Button
                            variant="quiet"
                            disabled={!metric}
                            onClick={() => {
                              mutation.reset();
                              setEditing(plan);
                            }}
                          >
                            Изменить
                          </Button>
                        )}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        ) : (
          <EmptyState
            icon="calendar"
            title="На этот месяц планы не заданы"
            description={
              stores.data?.length && metrics.data?.length
                ? "Добавьте согласованный план. Отсутствующее значение не будет считаться нулём на обзоре."
                : "Сначала добавьте точки и определите хотя бы один показатель."
            }
          />
        )}
      </Panel>
      {editing && (
        <Modal
          open
          onOpenChange={(open) => !open && setEditing(null)}
          title={current ? "Новая редакция плана" : "План на месяц"}
          description="План относится к конкретной точке, версии показателя и календарному месяцу."
        >
          <div className="modal-body">
            <Form
              onSubmit={(form) =>
                mutation.mutate({
                  store_id: current?.store_id ?? formText(form, "store_id"),
                  metric_id: current?.metric_id ?? formText(form, "metric_id"),
                  period: `${formText(form, "period")}-01`,
                  amount: formText(form, "amount"),
                })
              }
            >
              <Field label="Точка">
                <select
                  name="store_id"
                  required
                  defaultValue={current?.store_id ?? ""}
                  disabled={Boolean(current)}
                >
                  <option value="">Выберите точку</option>
                  {stores.data?.map((store) => (
                    <option value={store.id} key={store.id}>
                      {store.name}
                    </option>
                  ))}
                </select>
              </Field>
              <Field label="Показатель">
                <select
                  name="metric_id"
                  required
                  defaultValue={current?.metric_id ?? ""}
                  disabled={Boolean(current)}
                >
                  <option value="">Выберите показатель</option>
                  {metrics.data?.map((metric) => (
                    <option value={metric.id} key={metric.id}>
                      {metric.name} · {metric.unit} · v{metric.version}
                    </option>
                  ))}
                </select>
              </Field>
              <div className="form-grid">
                <Field label="Месяц">
                  <input
                    name="period"
                    type="month"
                    required
                    defaultValue={current?.period.slice(0, 7) ?? month}
                  />
                </Field>
                <Field label="Значение">
                  <input
                    name="amount"
                    inputMode="decimal"
                    required
                    pattern="[0-9]+([.][0-9]{1,4})?"
                    maxLength={25}
                    defaultValue={current?.amount}
                    placeholder="1500000.00"
                  />
                </Field>
              </div>
              <InlineError error={mutation.error} />
              <div className="form-actions">
                <Button variant="secondary" onClick={() => setEditing(null)}>
                  Отмена
                </Button>
                <Button type="submit" loading={mutation.isPending}>
                  Сохранить план
                </Button>
              </div>
            </Form>
          </div>
        </Modal>
      )}
    </>
  );
}
