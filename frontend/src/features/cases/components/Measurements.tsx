import { useRef, useState } from "react";
import { Link } from "react-router-dom";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useWorkspace } from "../../../app/workspace";
import { post, workspacePath } from "../../../shared/api/client";
import type { Measurement } from "../../../shared/api/contracts";
import { keys } from "../../../shared/api/queries";
import {
  Button,
  Field,
  Form,
  formText,
  InlineError,
  Modal,
  Panel,
} from "../../../shared/ui/Common";
import {
  dateTime,
  money,
  monthRange,
  percent,
  periodLabel,
  previousMonth,
} from "../../../shared/ui/format";
import { CalculationDetails } from "../../../shared/results/CalculationDetails";

export function Measurements({
  items,
  caseId,
  canMeasure,
}: {
  items: Measurement[];
  caseId: string;
  canMeasure: boolean;
}) {
  const { id } = useWorkspace();
  const client = useQueryClient();
  const [open, setOpen] = useState(false);
  const requestKey = useRef({ body: "", key: "" });
  const mutation = useMutation({
    mutationFn: (body: object) => {
      const signature = JSON.stringify(body);
      if (requestKey.current.body !== signature)
        requestKey.current = { body: signature, key: crypto.randomUUID() };
      return post(workspacePath(id, `/cases/${caseId}/measurements`), {
        ...body,
        idempotency_key: requestKey.current.key,
      });
    },
    onSuccess: () => {
      void client.invalidateQueries({
        queryKey: keys.resource(id, "case", caseId),
      });
      setOpen(false);
      requestKey.current = { body: "", key: "" };
    },
  });
  return (
    <>
      <Panel
        title="Зафиксированные измерения"
        description="У каждого снимка свой период и время расчёта. Исходные числа сохраняются."
        className="spaced"
        actions={
          canMeasure ? (
            <Button
              variant="secondary"
              icon="refresh"
              onClick={() => {
                mutation.reset();
                setOpen(true);
              }}
            >
              Повторить измерение
            </Button>
          ) : undefined
        }
      >
        <div className="measurement-list">
          {items.map((item, index) => (
            <article className="measurement-card" key={item.id}>
              <header>
                <div>
                  <span className="eyebrow">
                    {index === 0
                      ? "Исходное наблюдение"
                      : `Измерение ${index + 1}`}
                  </span>
                  <h3>{item.metric.name}</h3>
                  <p>{periodLabel(item.date_from, item.date_to)}</p>
                </div>
                <time>{dateTime(item.created_at)}</time>
              </header>
              <dl className="measurement-values">
                <div>
                  <dt>Факт</dt>
                  <dd>
                    {money(item.overview.totals.actual, item.metric.unit)}
                  </dd>
                </div>
                <div>
                  <dt>План</dt>
                  <dd>{money(item.overview.totals.plan, item.metric.unit)}</dd>
                </div>
                <div>
                  <dt>Выполнение</dt>
                  <dd>{percent(item.overview.totals.attainment)}</dd>
                </div>
                <div>
                  <dt>К прошлому периоду</dt>
                  <dd>{percent(item.overview.comparison?.change_percent)}</dd>
                </div>
              </dl>
              {index > 0 && item.change_from_initial && (
                <p className="measurement-change">
                  К исходному измерению по сопоставимым точкам:{" "}
                  <strong>
                    {money(item.change_from_initial.delta, item.metric.unit)}
                  </strong>
                  {item.change_from_initial.change_percent !== null
                    ? ` (${percent(item.change_from_initial.change_percent)})`
                    : ""}
                  . Сравниваются указанные периоды; изменение само по себе не
                  доказывает причину.
                </p>
              )}
              <div className="measurement-meta">
                <span>Определение v{item.metric.version}</span>
                <span>
                  Строки есть по {item.overview.coverage.available} из{" "}
                  {item.overview.coverage.total} точек
                </span>
                {item.store_ids.map((storeId) => (
                  <Link
                    key={storeId}
                    to={`/w/${id}/stores/${storeId}?month=${item.date_from.slice(0, 7)}&metric=${item.metric.id}`}
                  >
                    Открыть показатели точки
                  </Link>
                ))}
              </div>
              <details className="measurement-definition">
                <summary>Определение и ограничения</summary>
                <p>{item.metric.description}</p>
                {item.overview.warnings.map((warning, i) => (
                  <p className="warning-note" key={i}>
                    {warning}
                  </p>
                ))}
                {item.overview.calculation && (
                  <CalculationDetails calculation={item.overview.calculation} />
                )}
              </details>
            </article>
          ))}
        </div>
      </Panel>
      <Modal
        open={open}
        onOpenChange={setOpen}
        title="Повторить измерение"
        description="Тот же показатель и область точек, новый период. Генерация SQL не требуется."
      >
        <div className="modal-body">
          <Form
            onSubmit={(form) => {
              const range = monthRange(formText(form, "month"));
              mutation.mutate({ date_from: range.from, date_to: range.to });
            }}
          >
            <Field label="Период измерения">
              <input
                type="month"
                name="month"
                required
                defaultValue={previousMonth()}
              />
            </Field>
            <InlineError error={mutation.error} />
            <div className="form-actions">
              <Button variant="secondary" onClick={() => setOpen(false)}>
                Отмена
              </Button>
              <Button type="submit" loading={mutation.isPending}>
                Зафиксировать измерение
              </Button>
            </div>
          </Form>
        </div>
      </Modal>
    </>
  );
}
