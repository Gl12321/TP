import { useState } from "react";
import { Link, useParams, useSearchParams } from "react-router-dom";
import { useInfiniteQuery } from "@tanstack/react-query";
import { useWorkspace } from "../../../app/workspace";
import {
  getPage,
  queryString,
  workspacePath,
} from "../../../shared/api/client";
import type { Case } from "../../../shared/api/contracts";
import { keys } from "../../../shared/api/queries";
import {
  Button,
  EmptyState,
  ErrorState,
  Loading,
  PageHeader,
  Panel,
  StatusBadge,
} from "../../../shared/ui/Common";
import { dateTime } from "../../../shared/ui/format";
import { Icon } from "../../../shared/ui/Icon";
import { Pagination } from "../../../shared/ui/Pagination";
import { useDebouncedValue } from "../../../shared/ui/useDebouncedValue";

import { CaseCreateDialog } from "../components/CaseCreateDialog";
import { caseStatus } from "../model/status";

import { CaseView } from "../components/CaseView";

export function CasesPage() {
  const workspace = useWorkspace();
  const { caseId } = useParams();
  const [search, setSearch] = useSearchParams();
  const [create, setCreate] = useState(false);
  const filter = ["all", "mine", "waiting", "closed", "open"].includes(
    search.get("filter") ?? "",
  )
    ? search.get("filter")!
    : "all";
  const term = (search.get("q") ?? "").slice(0, 200);
  const settledTerm = useDebouncedValue(term.trim());
  const updateFilter = (name: string, value: string) =>
    setSearch(
      (previous) => {
        const next = new URLSearchParams(previous);
        if (value) next.set(name, value);
        else next.delete(name);
        return next;
      },
      { replace: name === "q" },
    );
  const query = useInfiniteQuery({
    queryKey: keys.resource(workspace.id, "cases", "infinite", {
      filter,
      q: settledTerm,
    }),
    initialPageParam: null as string | null,
    queryFn: ({ signal, pageParam }) =>
      getPage<Case>(
        workspacePath(
          workspace.id,
          `/cases${queryString({ filter, q: settledTerm, limit: 50, cursor: pageParam })}`,
        ),
        signal,
      ),
    getNextPageParam: (page) => page.nextCursor,
    enabled: !caseId,
  });
  if (caseId) return <CaseView caseId={caseId} />;
  const items = Array.from(
    new Map(
      query.data?.pages
        .flatMap((page) => page.items)
        .map((item) => [item.id, item]) ?? [],
    ).values(),
  );
  return (
    <>
      <PageHeader
        eyebrow="Работа с командой"
        title="Разборы"
        description="Конкретное наблюдение, вопрос ответственному и итог обсуждения — в одном месте."
        actions={
          workspace.can("cases:write") && (
            <Button icon="plus" onClick={() => setCreate(true)}>
              Новый разбор
            </Button>
          )
        }
      />
      <Panel>
        <div className="list-toolbar issue-toolbar">
          <div className="segmented" role="group" aria-label="Фильтр разборов">
            {[
              ["all", "Все"],
              ["mine", "Мои"],
              ["waiting", "Ждут моего ответа"],
              ["open", "В работе"],
              ["closed", "Завершённые"],
            ].map(([value, label]) => (
              <button
                type="button"
                key={value}
                aria-pressed={filter === value}
                className={filter === value ? "active" : ""}
                onClick={() => updateFilter("filter", value)}
              >
                {label}
              </button>
            ))}
          </div>
          <div className="search-input">
            <Icon name="search" size={17} />
            <input
              type="search"
              maxLength={200}
              value={term}
              onChange={(event) => updateFilter("q", event.target.value)}
              placeholder="Тема или описание"
              aria-label="Найти разбор"
            />
          </div>
        </div>
        {query.data && query.error && !query.isFetchNextPageError && (
          <ErrorState error={query.error} retry={() => void query.refetch()} />
        )}
        {query.isPending ? (
          <Loading />
        ) : query.error && !query.data ? (
          <ErrorState error={query.error} retry={() => void query.refetch()} />
        ) : items.length ? (
          <div className="object-list">
            {items.map((item) => (
              <Link
                className="object-row"
                key={item.id}
                to={{
                  pathname: `/w/${workspace.id}/cases/${item.id}`,
                  search: search.toString(),
                }}
              >
                <span className="object-symbol">
                  <Icon name="cases" />
                </span>
                <div>
                  <strong>{item.title}</strong>
                  <span>
                    {item.description || "Без дополнительного описания"}
                  </span>
                  <time dateTime={item.updated_at}>
                    {dateTime(item.updated_at)}
                  </time>
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
            title={
              query.hasNextPage
                ? "Проверим более ранние разборы"
                : term || filter !== "all"
                  ? "Подходящих разборов нет"
                  : "Здесь появятся ваши разборы"
            }
            description={
              query.hasNextPage
                ? "На этой странице нет доступных разборов. Загрузите следующую страницу."
                : term || filter !== "all"
                  ? "Измените поисковый запрос или выберите другой фильтр."
                  : "Создайте обсуждение по точке или сохраните конкретный результат из ассистента. Вопрос сотруднику будет связан с его основанием."
            }
            action={
              term || filter !== "all" ? (
                <Button
                  variant="secondary"
                  onClick={() =>
                    setSearch((previous) => {
                      const next = new URLSearchParams(previous);
                      next.delete("filter");
                      next.delete("q");
                      return next;
                    })
                  }
                >
                  Сбросить фильтры
                </Button>
              ) : undefined
            }
          />
        )}
        {query.data && (
          <Pagination
            count={items.length}
            hasMore={query.hasNextPage}
            loading={query.isFetchingNextPage}
            error={query.isFetchNextPageError ? query.error : undefined}
            onMore={() => void query.fetchNextPage()}
          />
        )}
      </Panel>
      {create && <CaseCreateDialog close={() => setCreate(false)} />}
    </>
  );
}
