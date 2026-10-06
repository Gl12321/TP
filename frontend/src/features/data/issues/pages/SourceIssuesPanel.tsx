import { useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { useInfiniteQuery } from "@tanstack/react-query";
import { useWorkspace } from "../../../../app/workspace";
import {
  getPage,
  queryString,
  workspacePath,
} from "../../../../shared/api/client";
import type { SourceIssue } from "../../../../shared/api/contracts";
import { keys } from "../../../../shared/api/queries";
import {
  Button,
  EmptyState,
  ErrorState,
  Loading,
  Panel,
  StatusBadge,
} from "../../../../shared/ui/Common";
import { dateTime } from "../../../../shared/ui/format";
import { Icon } from "../../../../shared/ui/Icon";
import { Pagination } from "../../../../shared/ui/Pagination";
import { useDebouncedValue } from "../../../../shared/ui/useDebouncedValue";

import { SourceIssueView } from "../components/SourceIssueView";
import { CreateIssue } from "../components/CreateIssue";
import { issueStatus } from "../model/status";

export function SourceIssuesPanel() {
  const workspace = useWorkspace();
  const [search, setSearch] = useSearchParams();
  const [create, setCreate] = useState(false);
  const filter = ["active", "mine", "resolved", "all"].includes(
    search.get("issue_status") ?? "",
  )
    ? search.get("issue_status")!
    : "active";
  const term = (search.get("issue_q") ?? "").slice(0, 200);
  const settledTerm = useDebouncedValue(term.trim());
  const setFilter = (value: string, name = "issue_status") =>
    setSearch(
      (previous) => {
        const next = new URLSearchParams(previous);
        if (value) next.set(name, value);
        else next.delete(name);
        return next;
      },
      { replace: name === "issue_q" },
    );
  const query = useInfiniteQuery({
    queryKey: keys.resource(workspace.id, "source-issues", "infinite", {
      filter,
      q: settledTerm,
    }),
    initialPageParam: null as string | null,
    queryFn: ({ signal, pageParam }) =>
      getPage<SourceIssue>(
        workspacePath(
          workspace.id,
          `/source-issues${queryString({ filter, q: settledTerm, limit: 50, cursor: pageParam })}`,
        ),
        signal,
      ),
    getNextPageParam: (page) => page.nextCursor,
    refetchInterval: 30_000,
    enabled: !search.get("issue"),
  });
  const selected = search.get("issue");
  const open = (id: string | null) =>
    setSearch((previous) => {
      const next = new URLSearchParams(previous);
      if (id) next.set("issue", id);
      else next.delete("issue");
      next.set("tab", "issues");
      return next;
    });
  const items = Array.from(
    new Map(
      query.data?.pages
        .flatMap((page) => page.items)
        .map((item) => [item.id, item]) ?? [],
    ).values(),
  );
  if (selected)
    return (
      <SourceIssueView
        key={selected}
        issueId={selected}
        close={() => open(null)}
      />
    );
  return (
    <>
      <Panel
        title="Проверка источников с командой"
        description="Если данные не обновились или источник недоступен, передайте наблюдение ответственному. Состояние подключения сохранится вместе с обращением."
        actions={
          <Button icon="plus" onClick={() => setCreate(true)}>
            Новое обращение
          </Button>
        }
      >
        <div className="list-toolbar issue-toolbar">
          <div className="segmented" role="group" aria-label="Фильтр обращений">
            {[
              ["active", "Открытые"],
              ["mine", "Мои"],
              ["resolved", "Решённые"],
              ["all", "Все"],
            ].map(([value, label]) => (
              <button
                type="button"
                key={value}
                className={filter === value ? "active" : ""}
                aria-pressed={filter === value}
                onClick={() => setFilter(value)}
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
              onChange={(event) => setFilter(event.target.value, "issue_q")}
              placeholder="Тема или источник"
              aria-label="Найти обращение"
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
                key={item.id}
                className="object-row issue-row"
                to={{
                  search: (() => {
                    const next = new URLSearchParams(search);
                    next.set("tab", "issues");
                    next.set("issue", item.id);
                    return next.toString();
                  })(),
                }}
              >
                <span className="object-symbol">
                  <Icon name="data" />
                </span>
                <div>
                  <strong>{item.title}</strong>
                  <span>{item.source_name}</span>
                  <time dateTime={item.updated_at}>
                    {dateTime(item.updated_at)}
                  </time>
                </div>
                <StatusBadge status={item.status}>
                  {issueStatus[item.status]}
                </StatusBadge>
                <Icon name="chevron" size={18} />
              </Link>
            ))}
          </div>
        ) : (
          <EmptyState
            icon="data"
            title={
              query.hasNextPage
                ? "Проверим более ранние обращения"
                : term.trim()
                  ? "Ничего не найдено"
                  : filter === "active"
                    ? "Открытых обращений нет"
                    : "Обращений по этому фильтру нет"
            }
            description={
              query.hasNextPage
                ? "На этой странице нет доступных обращений. Загрузите следующую страницу."
                : term.trim()
                  ? "Попробуйте другую тему или название источника."
                  : "Обращение связывает наблюдение, ответственного и подтверждение исправления. Оно доступно команде источника и техническим администраторам."
            }
            action={
              term || filter !== "all" ? (
                <Button
                  variant="secondary"
                  onClick={() =>
                    setSearch((previous) => {
                      const next = new URLSearchParams(previous);
                      next.set("issue_status", "all");
                      next.delete("issue_q");
                      return next;
                    })
                  }
                >
                  Показать все обращения
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
      {create && (
        <CreateIssue
          close={() => setCreate(false)}
          created={(id) => {
            setCreate(false);
            open(id);
          }}
        />
      )}
    </>
  );
}
