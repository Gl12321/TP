import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useSession } from "../../app/session";
import { useWorkspace } from "../../app/workspace";
import { get, patch, post, workspacePath } from "../../shared/api/client";
import type { Member, Role, Store } from "../../shared/api/contracts";
import { keys, useStores } from "../../shared/api/queries";
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
  PageHeader,
  Panel,
  roleLabels,
  StatusBadge,
} from "../../shared/ui/Common";
import { Icon } from "../../shared/ui/Icon";
import { initials } from "../../shared/ui/format";
import { InvitationsPanel } from "./InvitationsPanel";
import { SecurityPanel } from "./SecurityPanel";
import { CreateWorkspace } from "./CreateWorkspace";

const roleDescriptions: Record<Role, string> = {
  director:
    "Сравнивает доступные точки, задаёт планы и принимает решения по результатам разборов.",
  regional_manager:
    "Следит за назначенными точками, задаёт планы и координирует разборы с управляющими.",
  franchise_owner:
    "Работает с показателями своих точек, задаёт вопросы и участвует в разборах.",
  store_manager:
    "Анализирует свою точку и отвечает на предметные вопросы команды.",
  analyst:
    "Согласует определения показателей, готовит отчёты и исследует данные.",
  admin:
    "Управляет участниками, подключениями и разрешёнными таблицами. Доступ к аналитике назначается отдельно.",
};

export function SettingsPage() {
  const workspace = useWorkspace();
  const { session } = useSession();
  const [tab, setTab] = useState("profile");
  const health = useQuery({
    queryKey: ["health"],
    queryFn: ({ signal }) =>
      get<{ status: string; worker_ready: boolean }>("/health", signal),
    refetchInterval: 30_000,
  });
  return (
    <>
      <PageHeader
        eyebrow={workspace.name}
        title="Настройки пространства"
        description="Участники, полномочия и справочник точек. Роль определяет действия, область — доступные данные."
        actions={<CreateWorkspace />}
      />
      <nav className="page-tabs" aria-label="Разделы настроек">
        {[
          ["profile", "Мой доступ"],
          ...(workspace.can("members:manage")
            ? [
                ["members", "Команда"],
                ["stores", "Справочник точек"],
              ]
            : []),
        ].map(([key, label]) => (
          <button
            type="button"
            key={key}
            className={tab === key ? "active" : ""}
            aria-current={tab === key ? "page" : undefined}
            onClick={() => setTab(key)}
          >
            {label}
          </button>
        ))}
      </nav>
      {tab === "members" && workspace.can("members:manage") ? (
        <MembersPanel />
      ) : tab === "stores" && workspace.can("members:manage") ? (
        <StoreDirectory />
      ) : (
        <div className="two-column">
          <Panel title="Ваша учётная запись">
            <div className="panel-body">
              <div className="account-summary">
                <span className="avatar large">
                  {initials(session?.user.name ?? "")}
                </span>
                <div>
                  <h3>{session?.user.name}</h3>
                  <p>{session?.user.email}</p>
                </div>
              </div>
              <dl className="metadata-list">
                <div>
                  <dt>Пространство</dt>
                  <dd>{workspace.name}</dd>
                </div>
                <div>
                  <dt>Роль</dt>
                  <dd>{roleLabels[workspace.role]}</dd>
                </div>
                <div>
                  <dt>Область</dt>
                  <dd>
                    {workspace.all_stores
                      ? "Все точки пространства"
                      : `Назначено точек: ${workspace.store_ids.length}`}
                  </dd>
                </div>
                <div>
                  <dt>Ассистент</dt>
                  <dd>
                    {workspace.can("assistant:use")
                      ? "Доступен в вашей области данных"
                      : "Доступ не назначен"}
                  </dd>
                </div>
              </dl>
              <p className="body-copy">{roleDescriptions[workspace.role]}</p>
            </div>
          </Panel>
          <Panel title="Доступность приложения">
            <div className="panel-body">
              {health.isPending ? (
                <Loading />
              ) : health.error ? (
                <ErrorState
                  error={health.error}
                  retry={() => void health.refetch()}
                />
              ) : (
                <>
                  <div className="service-row">
                    <span>Приложение</span>
                    <StatusBadge
                      status={
                        health.data?.status === "ok" ? "ready" : "waiting"
                      }
                    >
                      {health.data?.status === "ok"
                        ? "Доступно"
                        : "Проверяется"}
                    </StatusBadge>
                  </div>
                  <div className="service-row">
                    <span>Обработка запросов</span>
                    <StatusBadge
                      status={health.data?.worker_ready ? "ready" : "waiting"}
                    >
                      {health.data?.worker_ready ? "Готова" : "Ожидает запуска"}
                    </StatusBadge>
                  </div>
                  <p className="subtle-note">
                    Сохранённые отчёты и обсуждения доступны независимо от
                    готовности модели. Новые вопросы обрабатываются в очереди.
                  </p>
                </>
              )}
            </div>
          </Panel>
          <SecurityPanel />
        </div>
      )}
    </>
  );
}

function MembersPanel() {
  const workspace = useWorkspace();
  const query = useQuery({
    queryKey: keys.resource(workspace.id, "members"),
    queryFn: ({ signal }) =>
      get<Member[]>(workspacePath(workspace.id, "/members"), signal),
  });
  const [editing, setEditing] = useState<Member | null>(null);
  const [invite, setInvite] = useState(false);
  const [search, setSearch] = useState("");
  const filtered =
    query.data?.filter((member) =>
      `${member.name} ${member.email}`
        .toLocaleLowerCase("ru")
        .includes(search.toLocaleLowerCase("ru")),
    ) ?? [];
  return (
    <>
      <div className="section-heading">
        <div>
          <h2>Команда пространства</h2>
          <p>Назначайте роль и область каждой учётной записи отдельно.</p>
        </div>
        <Button icon="plus" onClick={() => setInvite(true)}>
          Добавить участника
        </Button>
      </div>
      <Panel>
        <div className="list-toolbar">
          <div className="search-input">
            <Icon name="search" size={17} />
            <input
              value={search}
              onChange={(event) => setSearch(event.target.value)}
              placeholder="Имя или почта"
              aria-label="Найти участника"
            />
          </div>
          <span className="list-count">{filtered.length} участников</span>
        </div>
        {query.isPending ? (
          <Loading />
        ) : query.error ? (
          <ErrorState error={query.error} retry={() => void query.refetch()} />
        ) : filtered.length ? (
          <div
            className="table-scroll"
            role="region"
            aria-label="Участники пространства"
            tabIndex={0}
          >
            <table className="data-table">
              <caption className="sr-only">
                Команда и назначенные полномочия
              </caption>
              <thead>
                <tr>
                  <th>Участник</th>
                  <th>Роль</th>
                  <th>Область данных</th>
                  <th>Доступ</th>
                  <th>
                    <span className="sr-only">Действия</span>
                  </th>
                </tr>
              </thead>
              <tbody>
                {filtered.map((member) => (
                  <tr key={member.id}>
                    <td>
                      <div className="person-cell">
                        <span className="avatar">{initials(member.name)}</span>
                        <div>
                          <strong>{member.name}</strong>
                          <span>{member.email}</span>
                        </div>
                      </div>
                    </td>
                    <td>
                      {roleLabels[member.role]}
                      {member.owner && (
                        <span className="cell-subtitle">
                          Владелец пространства
                        </span>
                      )}
                    </td>
                    <td>
                      {!member.data_access
                        ? "Аналитика закрыта"
                        : member.all_stores
                          ? "Все точки"
                          : `${member.store_ids.length} назначенных точек`}
                    </td>
                    <td>
                      <StatusBadge
                        status={member.active ? "ready" : "inactive"}
                      >
                        {member.active ? "Активен" : "Отключён"}
                      </StatusBadge>
                    </td>
                    <td>
                      {!member.owner && (
                        <Button
                          variant="quiet"
                          onClick={() => setEditing(member)}
                        >
                          Изменить
                        </Button>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <EmptyState
            icon="search"
            title="Участники не найдены"
            description="Измените поисковый запрос."
          />
        )}
      </Panel>
      <InvitationsPanel open={invite} close={() => setInvite(false)} />
      {editing && (
        <MemberDialog member={editing} close={() => setEditing(null)} />
      )}
    </>
  );
}

function MemberDialog({
  member,
  close,
}: {
  member: Member;
  close: () => void;
}) {
  const workspace = useWorkspace();
  const { refresh } = useSession();
  const client = useQueryClient();
  const stores = useStores(workspace.id);
  const [role, setRole] = useState<Role>(member?.role ?? "store_manager");
  const [allStores, setAllStores] = useState(member?.all_stores ?? false);
  const [storeIds, setStoreIds] = useState<string[]>(member?.store_ids ?? []);
  const [dataAccess, setDataAccess] = useState(member?.data_access ?? true);
  const [active, setActive] = useState(member?.active ?? true);
  const [validation, setValidation] = useState<string | null>(null);
  const mutation = useMutation({
    mutationFn: (body: object) =>
      patch(workspacePath(workspace.id, `/members/${member.id}`), body),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: keys.workspace(workspace.id) });
      refresh();
      close();
    },
  });
  return (
    <Modal
      open
      onOpenChange={(open) => !open && close()}
      title={`Доступ · ${member.name}`}
      description="Изменение роли и области применяется к следующим действиям участника. Сохранённые результаты тоже проверяются по актуальным правам."
    >
      <div className="modal-body">
        <Form
          onSubmit={() => {
            if (dataAccess && !allStores && !storeIds.length) {
              setValidation(
                "Назначьте хотя бы одну точку или отключите доступ к аналитике.",
              );
              return;
            }
            setValidation(null);
            mutation.mutate({
              role,
              all_stores: allStores,
              store_ids: allStores ? [] : storeIds,
              data_access: dataAccess,
              active,
            });
          }}
        >
          <Field label="Роль">
            <select
              value={role}
              onChange={(event) => setRole(event.target.value as Role)}
            >
              {Object.entries(roleLabels).map(([value, label]) => (
                <option value={value} key={value}>
                  {label}
                </option>
              ))}
            </select>
          </Field>
          <p className="role-description">{roleDescriptions[role]}</p>
          <label className="check-label">
            <input
              type="checkbox"
              checked={dataAccess}
              onChange={(event) => setDataAccess(event.target.checked)}
            />
            <span>
              <strong>Доступ к аналитике и ассистенту</strong>
              <small>В пределах назначенной области данных.</small>
            </span>
          </label>
          {dataAccess && (
            <fieldset className="scope-fieldset">
              <legend>Область точек</legend>
              <label className="check-label">
                <input
                  type="checkbox"
                  checked={allStores}
                  onChange={(event) => setAllStores(event.target.checked)}
                />
                <span>Все точки, включая добавленные в будущем</span>
              </label>
              {!allStores && (
                <div className="store-choices">
                  {stores.isPending ? (
                    <Loading />
                  ) : stores.error ? (
                    <ErrorState error={stores.error} />
                  ) : stores.data?.length ? (
                    stores.data.map((store) => (
                      <label className="check-label" key={store.id}>
                        <input
                          type="checkbox"
                          checked={storeIds.includes(store.id)}
                          onChange={(event) =>
                            setStoreIds((previous) =>
                              event.target.checked
                                ? [...previous, store.id]
                                : previous.filter((id) => id !== store.id),
                            )
                          }
                        />
                        <span>
                          {store.name}
                          <small>
                            {store.city} · {store.code}
                          </small>
                        </span>
                      </label>
                    ))
                  ) : (
                    <p className="small muted">
                      Сначала добавьте точки в справочник.
                    </p>
                  )}
                </div>
              )}
            </fieldset>
          )}
          {member && (
            <label className="check-label">
              <input
                type="checkbox"
                checked={active}
                onChange={(event) => setActive(event.target.checked)}
              />
              <span>
                <strong>Учётная запись активна в пространстве</strong>
                <small>
                  При отключении сохранённые данные остаются, доступ сотрудника
                  прекращается.
                </small>
              </span>
            </label>
          )}
          <InlineError error={validation ?? mutation.error} />
          <div className="form-actions">
            <Button variant="secondary" onClick={close}>
              Отмена
            </Button>
            <Button type="submit" loading={mutation.isPending}>
              Сохранить доступ
            </Button>
          </div>
        </Form>
      </div>
    </Modal>
  );
}

function StoreDirectory() {
  const { id } = useWorkspace();
  const query = useStores(id);
  const [editing, setEditing] = useState<Store | "new" | null>(null);
  return (
    <>
      <div className="section-heading">
        <div>
          <h2>Справочник точек</h2>
          <p>Внешний код связывает точку с фактами из подключённой базы.</p>
        </div>
        <Button icon="plus" onClick={() => setEditing("new")}>
          Добавить точку
        </Button>
      </div>
      <Panel>
        {query.isPending ? (
          <Loading />
        ) : query.error ? (
          <ErrorState error={query.error} retry={() => void query.refetch()} />
        ) : query.data?.length ? (
          <div className="object-list">
            {query.data.map((store) => (
              <article className="object-row" key={store.id}>
                <span className="object-symbol">
                  <Icon name="stores" />
                </span>
                <div>
                  <strong>{store.name}</strong>
                  <span>
                    {store.city} · {store.code}
                    {store.owner_name ? ` · ${store.owner_name}` : ""}
                  </span>
                </div>
                <StatusBadge status={store.active ? "ready" : "inactive"}>
                  {store.active ? "Работает" : "Неактивна"}
                </StatusBadge>
                <Button variant="quiet" onClick={() => setEditing(store)}>
                  Изменить
                </Button>
              </article>
            ))}
          </div>
        ) : (
          <EmptyState
            icon="stores"
            title="Добавьте первую точку"
            description="Укажите название, город и внешний код. Затем можно назначать область участникам и сравнивать показатели."
          />
        )}
      </Panel>
      {editing && (
        <StoreDialog
          store={editing === "new" ? undefined : editing}
          close={() => setEditing(null)}
        />
      )}
    </>
  );
}

function StoreDialog({ store, close }: { store?: Store; close: () => void }) {
  const { id } = useWorkspace();
  const client = useQueryClient();
  const mutation = useMutation({
    mutationFn: (body: object) =>
      store
        ? patch(workspacePath(id, `/stores/${store.id}`), body)
        : post(workspacePath(id, "/stores"), body),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: keys.workspace(id) });
      close();
    },
  });
  return (
    <Modal
      open
      onOpenChange={(open) => !open && close()}
      title={store ? `Точка · ${store.name}` : "Добавить точку"}
      description="Внешний код должен совпадать со значением ключа точки в базе данных."
    >
      <div className="modal-body">
        <Form
          onSubmit={(form) =>
            mutation.mutate({
              name: formText(form, "name"),
              city: formText(form, "city"),
              owner_name: formText(form, "owner_name"),
              active: new FormData(form).has("active"),
              ...(!store ? { code: formText(form, "code") } : {}),
            })
          }
        >
          <Field label="Название">
            <input
              name="name"
              required
              maxLength={160}
              defaultValue={store?.name}
            />
          </Field>
          <div className="form-grid">
            <Field
              label="Внешний код"
              hint={
                store ? "Код уже используется для связи данных." : undefined
              }
            >
              <input
                name="code"
                required
                maxLength={120}
                defaultValue={store?.code}
                readOnly={Boolean(store)}
              />
            </Field>
            <Field label="Город">
              <input name="city" maxLength={160} defaultValue={store?.city} />
            </Field>
          </div>
          <Field label="Партнёр">
            <input
              name="owner_name"
              maxLength={160}
              defaultValue={store?.owner_name}
            />
          </Field>
          <label className="check-label">
            <input
              name="active"
              type="checkbox"
              defaultChecked={store?.active ?? true}
            />
            <span>Точка работает</span>
          </label>
          <InlineError error={mutation.error} />
          <div className="form-actions">
            <Button variant="secondary" onClick={close}>
              Отмена
            </Button>
            <Button type="submit" loading={mutation.isPending}>
              Сохранить
            </Button>
          </div>
        </Form>
      </div>
    </Modal>
  );
}
