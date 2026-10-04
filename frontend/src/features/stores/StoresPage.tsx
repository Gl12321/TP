import { useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { ColumnDef } from "@tanstack/react-table";
import { useFilters, useWorkspace } from "../../app/workspace";
import { get, post, workspacePath } from "../../shared/api/client";
import type { Case, Store } from "../../shared/api/contracts";
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
  StatusBadge,
} from "../../shared/ui/Common";
import { money, percent, periodLabel } from "../../shared/ui/format";
import { Icon } from "../../shared/ui/Icon";
import { DataTable } from "../../shared/results/DataTable";
import { ScopeFilters } from "../overview/ScopeFilters";
import { MetricCard, useOverview } from "../overview/OverviewPage";
import { CaseCreateDialog } from "../cases/CaseCreateDialog";
import { StoreDynamics } from "./StoreDynamics";
import { PeriodComparison } from "../overview/PeriodComparison";

export function StoresPage() {
  const workspace = useWorkspace();
  const query = useStores(workspace.id);
  const [search, setSearch] = useState("");
  const [city, setCity] = useState("");
  const [create, setCreate] = useState(false);
  const filtered = useMemo(
    () =>
      (query.data ?? []).filter(
        (store) =>
          (!city || store.city === city) &&
          `${store.name} ${store.code} ${store.owner_name} ${store.city}`
            .toLocaleLowerCase("ru")
            .includes(search.toLocaleLowerCase("ru")),
      ),
    [query.data, city, search],
  );
  const cities = [...new Set(query.data?.map((store) => store.city))]
    .filter(Boolean)
    .sort();
  const columns = useMemo<ColumnDef<Store, unknown>[]>(
    () => [
      {
        accessorKey: "name",
        header: "Точка",
        cell: (info) => (
          <Link
            className="table-link"
            to={`/w/${workspace.id}/stores/${info.row.original.id}`}
          >
            {info.row.original.name}
            <span>{info.row.original.code}</span>
          </Link>
        ),
      },
      { accessorKey: "city", header: "Город" },
      { accessorKey: "owner_name", header: "Партнёр" },
      {
        accessorKey: "active",
        header: "Статус",
        cell: (info) => (
          <StatusBadge status={info.row.original.active ? "ready" : "inactive"}>
            {info.row.original.active ? "Работает" : "Неактивна"}
          </StatusBadge>
        ),
      },
    ],
    [workspace.id],
  );
  return (
    <>
      <PageHeader
        eyebrow="Доступная вам область"
        title="Точки сети"
        description="Карточки точек, их показатели и текущие обсуждения."
        actions={
          workspace.can("sources:manage") && (
            <Button icon="plus" onClick={() => setCreate(true)}>
              Добавить точку
            </Button>
          )
        }
      />
      <Panel>
        <div className="list-toolbar">
          <div className="search-input">
            <Icon name="search" size={17} />
            <input
              aria-label="Найти точку"
              placeholder="Название, код или партнёр"
              value={search}
              onChange={(event) => setSearch(event.target.value)}
            />
          </div>
          <select
            aria-label="Фильтр по городу"
            value={city}
            onChange={(event) => setCity(event.target.value)}
          >
            <option value="">Все города</option>
            {cities.map((item) => (
              <option key={item}>{item}</option>
            ))}
          </select>
          <span className="list-count">{filtered.length} точек</span>
        </div>
        {query.isPending ? (
          <Loading />
        ) : query.error ? (
          <ErrorState error={query.error} retry={() => void query.refetch()} />
        ) : !query.data?.length ? (
          <EmptyState
            icon="stores"
            title="Точки ещё не добавлены"
            description={
              workspace.can("sources:manage")
                ? "Добавьте справочник точек. Внешний код свяжет каждую точку с её данными в источнике."
                : "Вам пока не назначены точки. Обратитесь к администратору пространства."
            }
          />
        ) : (
          <DataTable
            rows={filtered}
            columns={columns}
            caption="Каталог доступных точек"
            empty="По этим условиям точек не найдено."
          />
        )}
      </Panel>
      {create && <StoreCreateDialog close={() => setCreate(false)} />}
    </>
  );
}

export function StorePage() {
  const workspace = useWorkspace();
  const filters = useFilters();
  const { storeId } = useParams();
  const stores = useStores(workspace.id);
  const query = useOverview(storeId);
  const [createCase, setCreateCase] = useState(false);
  const cases = useQuery({
    queryKey: keys.resource(workspace.id, "cases"),
    queryFn: ({ signal }) =>
      get<Case[]>(workspacePath(workspace.id, "/cases"), signal),
  });
  const store = stores.data?.find((item) => item.id === storeId);
  if (stores.isPending) return <Loading />;
  if (stores.error) return <ErrorState error={stores.error} />;
  if (!store)
    return (
      <EmptyState
        icon="lock"
        title="Точка недоступна"
        description="Проверьте ссылку или доступ к этой точке."
      />
    );
  const row = query.data?.stores.find((item) => item.store_id === storeId);
  const relatedCases =
    cases.data?.filter((item) => item.store_ids.includes(store.id)) ?? [];
  return (
    <>
      <Link className="back-link" to={`/w/${workspace.id}/stores`}>
        <Icon name="back" size={16} />
        Все доступные точки
      </Link>
      <PageHeader
        eyebrow={`${store.city} · ${store.code}`}
        title={store.name}
        description={
          store.owner_name
            ? `Партнёр: ${store.owner_name}`
            : "Показатели и обсуждения этой точки."
        }
        actions={
          <>
            <StatusBadge status={store.active ? "ready" : "inactive"}>
              {store.active ? "Работает" : "Неактивна"}
            </StatusBadge>
            {workspace.can("cases:write") && (
              <Button icon="plus" onClick={() => setCreateCase(true)}>
                Создать разбор
              </Button>
            )}
          </>
        }
      />
      <ScopeFilters fixedStore={store.id} />
      {query.metrics.isPending ? (
        <Loading />
      ) : query.metrics.error ? (
        <ErrorState error={query.metrics.error} />
      ) : !query.metrics.data?.length ? (
        <Panel>
          <EmptyState
            icon="data"
            title="Показатель пока не настроен"
            description="После подключения источника и определения показателя здесь появится расчёт по точке."
          />
        </Panel>
      ) : query.error ? (
        <ErrorState error={query.error} retry={() => void query.refetch()} />
      ) : query.isPending ? (
        <Loading />
      ) : (
        <>
          <div className="metric-cards">
            <MetricCard
              label={query.data?.metric?.name ?? "Факт"}
              value={money(row?.actual, query.data?.metric?.unit ?? "")}
              foot={periodLabel(filters.from, filters.to)}
              icon="overview"
            />
            <MetricCard
              label="План"
              value={money(row?.plan, query.data?.metric?.unit ?? "")}
              foot="Согласованный план на период"
              icon="reports"
            />
            <MetricCard
              label="Выполнение плана"
              value={percent(row?.attainment)}
              foot={
                row?.actual === null
                  ? "Нет факта для сравнения"
                  : "Расчёт по данным сервера"
              }
              icon="stores"
            />
          </div>
          {query.data?.comparison && (
            <PeriodComparison
              comparison={query.data.comparison}
              unit={query.data.metric?.unit ?? ""}
            />
          )}
          {query.metricId && (
            <StoreDynamics
              storeId={store.id}
              metricId={query.metricId}
              dateFrom={filters.from}
              dateTo={filters.to}
            />
          )}
          {query.data?.warnings.map((warning, index) => (
            <p key={index} className="warning-note">
              <Icon name="alert" size={16} />
              {warning}
            </p>
          ))}
        </>
      )}
      <div className="two-column">
        <Panel
          title="Разборы этой точки"
          description="Факты, вопросы сотрудникам и зафиксированные итоги."
        >
          {cases.isPending ? (
            <Loading />
          ) : cases.error ? (
            <ErrorState error={cases.error} />
          ) : relatedCases.length ? (
            <div className="object-list">
              {relatedCases.map((item) => (
                <Link
                  className="object-row"
                  key={item.id}
                  to={`/w/${workspace.id}/cases/${item.id}`}
                >
                  <div>
                    <strong>{item.title}</strong>
                    <span>{item.description || "Без описания"}</span>
                  </div>
                  <StatusBadge status={item.status}>
                    {caseStatus(item.status)}
                  </StatusBadge>
                  <Icon name="chevron" size={18} />
                </Link>
              ))}
            </div>
          ) : (
            <EmptyState
              icon="cases"
              title="Обсуждений пока нет"
              description="Сохраните конкретное наблюдение и задайте предметный вопрос ответственному сотруднику."
            />
          )}
        </Panel>
        <Panel title="Вопрос по этой точке">
          <div className="panel-body">
            <p className="body-copy">
              Уточните нужную таблицу обычными словами. Область нового разговора
              будет ограничена этой точкой.
            </p>
            {workspace.can("assistant:use") && (
              <Link
                className="button primary full-width"
                to={`/w/${workspace.id}/assistant?stores=${store.id}&month=${filters.month}`}
              >
                <Icon name="assistant" size={17} />
                Спросить по данным
              </Link>
            )}
            <div className="info-note">
              <Icon name="lock" size={17} />
              <span>
                Готовый результат сохраняет собственный период. Комментарии
                коллег не изменяют цифры источника.
              </span>
            </div>
          </div>
        </Panel>
      </div>
      {createCase && (
        <CaseCreateDialog
          close={() => setCreateCase(false)}
          storeId={store.id}
          measurement={
            query.data?.metric
              ? {
                  metric_id: query.data.metric.id,
                  date_from: filters.from,
                  date_to: filters.to,
                }
              : undefined
          }
          description={`Период: ${periodLabel(filters.from, filters.to)}. Точка: ${store.name}.`}
        />
      )}
    </>
  );
}

export function caseStatus(status: string) {
  return (
    (
      {
        open: "Открыт",
        draft: "Сохранён",
        waiting: "Ждём ответа",
        answered: "Ответ получен",
        closed: "Итог зафиксирован",
        done: "Итог зафиксирован",
      } as Record<string, string>
    )[status] ?? status
  );
}

function StoreCreateDialog({ close }: { close: () => void }) {
  const { id } = useWorkspace();
  const client = useQueryClient();
  const mutation = useMutation({
    mutationFn: (body: object) => post(workspacePath(id, "/stores"), body),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: keys.resource(id, "stores") });
      close();
    },
  });
  return (
    <Modal
      open
      onOpenChange={(open) => !open && close()}
      title="Добавить точку"
      description="Код должен совпадать со значением точки в подключаемой базе."
    >
      <div className="modal-body">
        <Form
          onSubmit={(form) =>
            mutation.mutate({
              name: formText(form, "name"),
              code: formText(form, "code"),
              city: formText(form, "city"),
              owner_name: formText(form, "owner_name"),
              active: true,
            })
          }
        >
          <Field label="Название">
            <input name="name" required maxLength={160} />
          </Field>
          <div className="form-grid">
            <Field label="Внешний код">
              <input name="code" required maxLength={100} />
            </Field>
            <Field label="Город">
              <input name="city" required maxLength={100} />
            </Field>
          </div>
          <Field label="Партнёр">
            <input name="owner_name" maxLength={160} />
          </Field>
          <InlineError error={mutation.error} />
          <div className="form-actions">
            <Button variant="secondary" onClick={close}>
              Отмена
            </Button>
            <Button type="submit" loading={mutation.isPending}>
              Добавить точку
            </Button>
          </div>
        </Form>
      </div>
    </Modal>
  );
}
