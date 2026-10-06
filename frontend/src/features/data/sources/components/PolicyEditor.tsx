import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useWorkspace } from "../../../../app/workspace";
import { put, workspacePath } from "../../../../shared/api/client";
import type {
  CatalogTable,
  TablePolicy,
} from "../../../../shared/api/contracts";
import { keys } from "../../../../shared/api/queries";
import { Button, Field, Form, InlineError } from "../../../../shared/ui/Common";
import { Icon } from "../../../../shared/ui/Icon";

import { policyDraft } from "../model/policies";
import { validatePolicies } from "../model/policies";

export function PolicyEditor({
  sourceId,
  tables,
  close,
}: {
  sourceId: string;
  tables: CatalogTable[];
  close: () => void;
}) {
  const { id } = useWorkspace();
  const client = useQueryClient();
  const [draft, setDraft] = useState(() => policyDraft(tables));
  const [validation, setValidation] = useState<string | null>(null);
  const mutation = useMutation({
    mutationFn: () =>
      put(workspacePath(id, `/sources/${sourceId}/policies`), {
        tables: draft
          .filter((table) => table.enabled)
          .map(({ enabled: _enabled, ...table }) => table),
      }),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: keys.workspace(id) });
      close();
    },
  });
  const change = (
    index: number,
    update: Partial<TablePolicy> & { enabled?: boolean },
  ) =>
    setDraft((previous) =>
      previous.map((table, i) =>
        i === index ? { ...table, ...update } : table,
      ),
    );
  return (
    <Form
      onSubmit={() => {
        const error = validatePolicies(draft);
        setValidation(error);
        if (!error) mutation.mutate();
      }}
    >
      <div className="policy-list">
        {tables.map((table, index) => (
          <section
            className={`policy-table ${draft[index].enabled ? "enabled" : ""}`}
            key={`${table.schema}.${table.name}`}
          >
            <label className="check-label policy-title">
              <input
                type="checkbox"
                checked={draft[index].enabled}
                onChange={(event) =>
                  change(index, { enabled: event.target.checked })
                }
              />
              <span>
                <strong>
                  {table.schema}.{table.name}
                </strong>
                <small>
                  {table.columns.length} полей ·{" "}
                  {draft[index].enabled
                    ? "разрешена после сохранения"
                    : "доступ закрыт"}
                </small>
              </span>
            </label>
            {draft[index].enabled && (
              <div className="policy-detail">
                <fieldset>
                  <legend>Разрешённые поля</legend>
                  <div className="column-choices">
                    {table.columns.map((column) => (
                      <label className="check-label" key={column.name}>
                        <input
                          type="checkbox"
                          checked={draft[index].columns.includes(column.name)}
                          onChange={(event) =>
                            change(index, {
                              columns: event.target.checked
                                ? [...draft[index].columns, column.name]
                                : draft[index].columns.filter(
                                    (name) => name !== column.name,
                                  ),
                            })
                          }
                        />
                        <span>
                          {column.name}
                          <small>{column.data_type}</small>
                        </span>
                      </label>
                    ))}
                  </div>
                </fieldset>
                <Field label="Как ограничивается доступ к строкам">
                  <select
                    value={draft[index].shared ? "shared" : "store"}
                    onChange={(event) =>
                      change(index, {
                        shared: event.target.value === "shared",
                        store_column: null,
                      })
                    }
                  >
                    <option value="store">
                      Факты · ограничивать по кодам точек
                    </option>
                    <option value="shared">
                      Общий справочник · все строки доступны
                    </option>
                  </select>
                </Field>
                {draft[index].shared ? (
                  <p className="warning-note">
                    <Icon name="alert" size={16} />
                    Все строки этой таблицы будут доступны всем пользователям с
                    доступом к аналитике. Не используйте этот режим для продаж и
                    других данных отдельных точек.
                  </p>
                ) : (
                  <Field
                    label="Поле внешнего кода точки"
                    hint="Значения должны совпадать с кодами в справочнике точек."
                  >
                    <select
                      value={draft[index].store_column ?? ""}
                      onChange={(event) =>
                        change(index, {
                          store_column: event.target.value || null,
                        })
                      }
                      required
                    >
                      <option value="">Выберите поле</option>
                      {table.columns
                        .filter((column) =>
                          draft[index].columns.includes(column.name),
                        )
                        .map((column) => (
                          <option key={column.name} value={column.name}>
                            {column.name}
                          </option>
                        ))}
                    </select>
                  </Field>
                )}
              </div>
            )}
          </section>
        ))}
      </div>
      <InlineError error={validation ?? mutation.error} />
      <div className="form-actions sticky-actions">
        <span className="small muted">
          Разрешено таблиц: {draft.filter((table) => table.enabled).length}
        </span>
        <Button variant="secondary" onClick={close}>
          Отмена
        </Button>
        <Button type="submit" loading={mutation.isPending}>
          Сохранить доступ
        </Button>
      </div>
    </Form>
  );
}
