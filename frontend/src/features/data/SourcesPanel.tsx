import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useWorkspace } from "../../app/workspace";
import { get, patch, post, put, workspacePath } from "../../shared/api/client";
import type {
  CatalogTable,
  Source,
  TablePolicy,
  Member,
} from "../../shared/api/contracts";
import { keys, useSources } from "../../shared/api/queries";
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
  StatusBadge,
  roleLabels,
} from "../../shared/ui/Common";
import { Icon } from "../../shared/ui/Icon";
import { dateTime } from "../../shared/ui/format";

export function SourcesPanel() {
  const { id } = useWorkspace();
  const sources = useSources(id);
  const [create, setCreate] = useState(false);
  const [editing, setEditing] = useState<Source | null>(null);
  const [catalogSource, setCatalogSource] = useState<Source | null>(null);
  return (
    <>
      <div className="section-heading">
        <div>
          <h2>Подключённые базы</h2>
          <p>
            Доступ только для чтения. Вы сами выбираете разрешённые таблицы и
            поля.
          </p>
        </div>
        <Button icon="plus" onClick={() => setCreate(true)}>
          Подключить PostgreSQL
        </Button>
      </div>
      <div className="setup-steps">
        <div>
          <span>01</span>
          <strong>Подключите базу</strong>
          <p>Отдельная учётная запись для чтения отчётных данных.</p>
        </div>
        <div>
          <span>02</span>
          <strong>Определите доступ</strong>
          <p>Разрешите поля и укажите связь фактов с кодами точек.</p>
        </div>
        <div>
          <span>03</span>
          <strong>Согласуйте показатели</strong>
          <p>Задайте, какие значения считать и по какой дате.</p>
        </div>
      </div>
      {sources.isPending ? (
        <Loading />
      ) : sources.error ? (
        <ErrorState
          error={sources.error}
          retry={() => void sources.refetch()}
        />
      ) : sources.data?.length ? (
        <div className="source-grid">
          {sources.data.map((source) => (
            <SourceCard
              key={source.id}
              source={source}
              onCatalog={() => setCatalogSource(source)}
              onEdit={() => setEditing(source)}
            />
          ))}
        </div>
      ) : (
        <Panel>
          <EmptyState
            icon="data"
            title="Источник ещё не подключён"
            description="Подключите отчётную базу PostgreSQL. После проверки соединения откроется каталог таблиц для настройки доступа."
            action={
              <Button
                variant="secondary"
                icon="plus"
                onClick={() => setCreate(true)}
              >
                Добавить источник
              </Button>
            }
          />
        </Panel>
      )}
      {(create || editing) && (
        <SourceCreateDialog
          source={editing ?? undefined}
          close={() => {
            setCreate(false);
            setEditing(null);
          }}
        />
      )}
      {catalogSource && (
        <CatalogDialog
          source={catalogSource}
          close={() => setCatalogSource(null)}
        />
      )}
    </>
  );
}

function SourceCard({
  source,
  onCatalog,
  onEdit,
}: {
  source: Source;
  onCatalog: () => void;
  onEdit: () => void;
}) {
  const { id } = useWorkspace();
  const client = useQueryClient();
  const test = useMutation({
    mutationFn: () =>
      post<Source>(workspacePath(id, `/sources/${source.id}/test`)),
    onSuccess: () =>
      client.invalidateQueries({ queryKey: keys.resource(id, "sources") }),
  });
  const error =
    source.error &&
    (typeof source.error === "string" ? source.error : source.error.message);
  const status = !source.enabled
    ? "Отключён"
    : source.status === "ready"
      ? "Подключён"
      : source.status === "error"
        ? "Ошибка подключения"
        : "Нужна проверка";
  return (
    <Panel className="source-card">
      <div className="source-card-top">
        <span className="object-symbol">
          <Icon name="data" size={23} />
        </span>
        <StatusBadge status={source.enabled ? source.status : "inactive"}>
          {status}
        </StatusBadge>
      </div>
      <h3>{source.name}</h3>
      <p className="source-address">
        {source.host}:{source.port} / {source.database}
      </p>
      <dl className="metadata-list">
        <div>
          <dt>Схемы</dt>
          <dd>{source.schemas.join(", ")}</dd>
        </div>
        <div>
          <dt>Пользователь БД</dt>
          <dd>{source.username}</dd>
        </div>
        <div>
          <dt>Последняя проверка</dt>
          <dd>{dateTime(source.last_checked_at)}</dd>
        </div>
        <div>
          <dt>Версия каталога</dt>
          <dd>{source.catalog_version}</dd>
        </div>
        <div>
          <dt>Кто читает данные</dt>
          <dd>
            {source.reader_ids == null
              ? "Все участники с доступом к данным"
              : source.reader_ids.length
                ? `Выбранные участники: ${source.reader_ids.length}`
                : "Никто — профиль закрыт"}
          </dd>
        </div>
      </dl>
      {error && (
        <p className="form-error" role="alert">
          {error}
        </p>
      )}
      <InlineError error={test.error} />
      <div className="source-actions">
        <Button
          variant="secondary"
          icon="refresh"
          loading={test.isPending}
          onClick={() => test.mutate()}
        >
          Проверить
        </Button>
        <Button
          variant="quiet"
          icon="lock"
          disabled={!source.catalog_version}
          onClick={onCatalog}
        >
          Таблицы и доступ
        </Button>
        <Button
          variant="quiet"
          onClick={onEdit}
          icon="settings"
          aria-label={`Изменить подключение ${source.name}`}
        />
      </div>
    </Panel>
  );
}

function SourceCreateDialog({
  source,
  close,
}: {
  source?: Source;
  close: () => void;
}) {
  const { id } = useWorkspace();
  const client = useQueryClient();
  const [readers, setReaders] = useState<string[] | null>(
    source?.reader_ids ?? null,
  );
  const members = useQuery({
    queryKey: keys.resource(id, "members"),
    queryFn: ({ signal }) =>
      get<Member[]>(workspacePath(id, "/members"), signal),
  });
  const mutation = useMutation({
    mutationFn: (body: object) =>
      source
        ? patch<Source>(workspacePath(id, `/sources/${source.id}`), body)
        : post<Source>(workspacePath(id, "/sources"), body),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: keys.resource(id, "sources") });
      close();
    },
  });
  return (
    <Modal
      open
      onOpenChange={(open) => !open && close()}
      title={source ? `Подключение · ${source.name}` : "Подключить PostgreSQL"}
      description="Подготовьте отдельного пользователя БД с правом SELECT на отчётные таблицы. Пароль не возвращается в браузер."
    >
      <div className="modal-body">
        <Form
          onSubmit={(form) => {
            const values: Record<string, unknown> = {
              name: formText(form, "name"),
              host: formText(form, "host"),
              port: Number(formText(form, "port")),
              database: formText(form, "database"),
              username: formText(form, "username"),
              schemas: formText(form, "schemas")
                .split(",")
                .map((value) => value.trim())
                .filter(Boolean),
              ssl_mode: formText(form, "ssl_mode"),
              reader_ids: readers,
            };
            if (source) values.enabled = new FormData(form).has("enabled");
            const password = String(new FormData(form).get("password") ?? "");
            if (password) values.password = password;
            const body = source
              ? Object.fromEntries(
                  Object.entries(values).filter(
                    ([key, value]) =>
                      JSON.stringify(value) !==
                      JSON.stringify(source[key as keyof Source]),
                  ),
                )
              : values;
            if (source && !Object.keys(body).length) {
              close();
              return;
            }
            mutation.mutate(body);
          }}
        >
          <Field label="Название источника">
            <input
              name="name"
              required
              maxLength={160}
              placeholder="Продажи сети"
              defaultValue={source?.name}
            />
          </Field>
          <div className="form-grid">
            <Field label="Адрес сервера">
              <input
                name="host"
                required
                maxLength={253}
                placeholder="db.company.ru"
                autoComplete="off"
                defaultValue={source?.host}
              />
            </Field>
            <Field label="Порт">
              <input
                name="port"
                type="number"
                required
                min={1}
                max={65535}
                defaultValue={source?.port ?? 5432}
              />
            </Field>
          </div>
          <Field label="База данных">
            <input
              name="database"
              required
              maxLength={128}
              defaultValue={source?.database}
            />
          </Field>
          <div className="form-grid">
            <Field label="Пользователь для чтения">
              <input
                name="username"
                required
                maxLength={128}
                autoComplete="off"
                defaultValue={source?.username}
              />
            </Field>
            <Field
              label="Пароль пользователя БД"
              hint={
                source
                  ? "Оставьте пустым, чтобы сохранить текущий пароль."
                  : undefined
              }
            >
              <input
                name="password"
                type="password"
                required={!source}
                maxLength={1024}
                autoComplete="new-password"
              />
            </Field>
          </div>
          <Field
            label="Схемы через запятую"
            hint="Укажите только схемы, предназначенные для аналитики."
          >
            <input
              name="schemas"
              required
              placeholder="reporting"
              defaultValue={source?.schemas.join(", ")}
            />
          </Field>
          <Field label="Защита соединения">
            <select
              name="ssl_mode"
              defaultValue={source?.ssl_mode ?? "require"}
            >
              <option value="require">TLS обязателен</option>
              <option value="verify-full">
                TLS с проверкой сертификата и имени
              </option>
              <option value="disable">
                Без TLS · только доверенная локальная сеть
              </option>
            </select>
          </Field>
          <Field
            label="Кто читает этот профиль"
            hint="Доступ к подключению и право читать его данные назначаются отдельно. Область точек каждого участника сохраняется."
          >
            <select
              value={readers === null ? "all" : "selected"}
              onChange={(event) =>
                setReaders(event.target.value === "all" ? null : [])
              }
            >
              <option value="all">Все участники с доступом к данным</option>
              <option value="selected">Только выбранные участники</option>
            </select>
          </Field>
          {readers !== null && (
            <div className="scope-options">
              {members.isPending ? (
                <Loading />
              ) : members.error ? (
                <InlineError error={members.error} />
              ) : (
                members.data
                  ?.filter((member) => member.active)
                  .map((member) => (
                    <label className="check-label" key={member.user_id}>
                      <input
                        type="checkbox"
                        checked={readers.includes(member.user_id)}
                        onChange={(event) =>
                          setReaders(
                            event.target.checked
                              ? [...readers, member.user_id]
                              : readers.filter(
                                  (value) => value !== member.user_id,
                                ),
                          )
                        }
                      />
                      <span>
                        {member.name}
                        <small>{roleLabels[member.role]}</small>
                      </span>
                    </label>
                  ))
              )}
              {!readers.length && (
                <p className="subtle-note">
                  Никто не сможет читать этот профиль, пока вы не выберете
                  участников.
                </p>
              )}
            </div>
          )}
          {source && (
            <>
              <label className="check-label">
                <input
                  name="enabled"
                  type="checkbox"
                  defaultChecked={source.enabled}
                />
                <span>Источник доступен для работы</span>
              </label>
              <p className="subtle-note">
                После изменения подключения повторите проверку. Сохранённые
                результаты с изменённой областью доступа могут стать недоступны.
              </p>
            </>
          )}
          <InlineError error={mutation.error} />
          <div className="form-actions">
            <Button variant="secondary" onClick={close}>
              Отмена
            </Button>
            <Button type="submit" loading={mutation.isPending}>
              Сохранить подключение
            </Button>
          </div>
        </Form>
      </div>
    </Modal>
  );
}

export type PolicyDraft = {
  schema: string;
  name: string;
  enabled: boolean;
  columns: string[];
  store_column: string | null;
  shared: boolean;
};

export function policyDraft(tables: CatalogTable[]): PolicyDraft[] {
  return tables.map((table) => ({
    schema: table.schema,
    name: table.name,
    enabled: Boolean(table.policy),
    columns: table.policy?.columns ?? [],
    store_column: table.policy?.store_column ?? null,
    shared: table.policy?.shared ?? false,
  }));
}

export function validatePolicies(draft: PolicyDraft[]): string | null {
  for (const table of draft.filter((item) => item.enabled)) {
    if (!table.columns.length)
      return `Выберите разрешённые поля в ${table.schema}.${table.name}.`;
    if (
      !table.shared &&
      (!table.store_column || !table.columns.includes(table.store_column))
    )
      return `Укажите разрешённое поле кода точки в ${table.schema}.${table.name}.`;
  }
  return null;
}

function CatalogDialog({
  source,
  close,
}: {
  source: Source;
  close: () => void;
}) {
  const { id } = useWorkspace();
  const query = useQuery({
    queryKey: keys.resource(id, "catalog", source.id),
    queryFn: ({ signal }) =>
      get<CatalogTable[]>(
        workspacePath(id, `/sources/${source.id}/catalog`),
        signal,
      ),
  });
  return (
    <Modal
      open
      onOpenChange={(open) => !open && close()}
      title={`Доступ к данным · ${source.name}`}
      description="Неразрешённые таблицы и поля не участвуют в аналитике. Для фактов укажите поле, содержащее внешний код точки."
      wide
    >
      <div className="modal-body">
        {query.isPending ? (
          <Loading />
        ) : query.error ? (
          <ErrorState error={query.error} retry={() => void query.refetch()} />
        ) : query.data?.length ? (
          <PolicyEditor
            sourceId={source.id}
            tables={query.data}
            close={close}
          />
        ) : (
          <EmptyState
            icon="data"
            title="Таблицы не найдены"
            description="Проверьте список схем и права учётной записи базы, затем повторите проверку подключения."
          />
        )}
      </div>
    </Modal>
  );
}

function PolicyEditor({
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
