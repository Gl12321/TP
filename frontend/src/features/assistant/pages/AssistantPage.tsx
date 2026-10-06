import { useEffect, useRef, useState } from "react";
import {
  Link,
  useNavigate,
  useParams,
  useSearchParams,
} from "react-router-dom";
import {
  useInfiniteQuery,
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";
import { useFilters, useWorkspace } from "../../../app/workspace";
import { useSession } from "../../../app/session";
import {
  get,
  getPage,
  patch,
  post,
  queryString,
  workspacePath,
} from "../../../shared/api/client";
import type {
  Conversation,
  ConversationDetail,
  Run,
} from "../../../shared/api/contracts";
import { activeRun } from "../../../shared/api/contracts";
import {
  keys,
  useMetrics,
  useSources,
  useStores,
} from "../../../shared/api/queries";
import {
  Button,
  ErrorState,
  InlineError,
  Loading,
  PageHeader,
  Modal,
  Form,
  Field,
  formText,
} from "../../../shared/ui/Common";
import { Icon } from "../../../shared/ui/Icon";
import { dateTime, monthRange, periodLabel } from "../../../shared/ui/format";
import { RunCard } from "../components/RunCard";
import { useDraft } from "../../../shared/ui/useDraft";
import { useDebouncedValue } from "../../../shared/ui/useDebouncedValue";

export function AssistantPage() {
  const workspace = useWorkspace();
  const { session } = useSession();
  const { conversationId } = useParams();
  const [search, setSearch] = useSearchParams();
  const navigate = useNavigate();
  const client = useQueryClient();
  const filters = useFilters();
  const sources = useSources(workspace.id);
  const stores = useStores(workspace.id);
  const metrics = useMetrics(workspace.id);
  const historySearch = (search.get("q") ?? "").slice(0, 200);
  const historyQuery = useDebouncedValue(historySearch.trim());
  const historyLinkSearch = queryString({ q: historySearch });
  const conversations = useInfiniteQuery({
    queryKey: keys.resource(
      workspace.id,
      "conversations",
      "infinite",
      historyQuery,
    ),
    queryFn: ({ signal, pageParam }) =>
      getPage<Conversation>(
        workspacePath(
          workspace.id,
          `/conversations${queryString({ q: historyQuery, cursor: pageParam, limit: 50 })}`,
        ),
        signal,
      ),
    initialPageParam: null as string | null,
    getNextPageParam: (last) => last.nextCursor,
  });
  const conversationItems = [
    ...new Map(
      (conversations.data?.pages.flatMap((page) => page.items) ?? []).map(
        (item) => [item.id, item],
      ),
    ).values(),
  ];
  const conversation = useQuery({
    queryKey: keys.resource(workspace.id, "conversation", conversationId),
    queryFn: ({ signal }) =>
      get<ConversationDetail>(
        workspacePath(workspace.id, `/conversations/${conversationId}`),
        signal,
      ),
    enabled: Boolean(conversationId),
    refetchInterval: (query) =>
      query.state.data?.runs.some((run) => activeRun(run.status))
        ? 2500
        : false,
  });
  const [question, setQuestion] = useDraft(
    `assistant:${conversationId ?? "new"}`,
    "",
  );
  const [renameOpen, setRenameOpen] = useState(false);
  const rename = useMutation({
    mutationFn: (title: string) =>
      patch(workspacePath(workspace.id, `/conversations/${conversationId}`), {
        title,
      }),
    onSuccess: () => {
      void client.invalidateQueries({
        queryKey: keys.resource(workspace.id, "conversations"),
      });
      void client.invalidateQueries({
        queryKey: keys.resource(workspace.id, "conversation", conversationId),
      });
      setRenameOpen(false);
    },
  });
  const [historyOpen, setHistoryOpen] = useState(false);
  const [sourceId, setSourceId] = useState("");
  const [storeIds, setStoreIds] = useState<string[]>(filters.storeIds);
  const [month, setMonth] = useState(search.has("month") ? filters.month : "");
  const [metricId, setMetricId] = useState("");
  const [baseRun, setBaseRun] = useState<Run | null>(null);
  const input = useRef<HTMLTextAreaElement>(null);
  const initialized = useRef<string | undefined>(undefined);
  const requestKey = useRef({ body: "", key: "" });
  const currentConversationId = useRef(conversationId);
  currentConversationId.current = conversationId;
  const active = conversation.data?.runs.find((run) => activeRun(run.status));
  const availableSources =
    sources.data?.filter(
      (source) =>
        source.enabled &&
        source.status === "ready" &&
        (source.reader_ids == null ||
          source.reader_ids.includes(session?.user.id ?? "")) &&
        (source.permitted_table_count === undefined ||
          source.permitted_table_count > 0),
    ) ?? [];
  const sourceAvailable = availableSources.some(
    (source) => source.id === sourceId,
  );
  useEffect(() => {
    if (!sourceId && availableSources.length)
      setSourceId(availableSources[0].id);
  }, [sourceId, availableSources]);
  useEffect(() => {
    if (initialized.current === conversationId) return;
    if (conversationId && !conversation.data) return;
    initialized.current = conversationId;
    setBaseRun(null);
    const last = conversation.data?.runs.at(-1);
    if (last) {
      setSourceId(last.source_id);
      setStoreIds(last.store_ids);
      setMonth(last.context?.date_from?.slice(0, 7) ?? "");
      setMetricId(last.context?.metric_id ?? "");
    }
  }, [conversationId, conversation.data]);
  const mutation = useMutation({
    mutationFn: async () => {
      const draftOrigin = conversationId ?? "new";
      let target = conversation.data;
      if (!target) {
        const created = await post<Conversation>(
          workspacePath(workspace.id, "/conversations"),
          { title: question.slice(0, 100) },
        );
        target = { ...created, messages: [], runs: [] };
        client.setQueryData(
          keys.resource(workspace.id, "conversation", created.id),
          target,
        );
        navigate(
          `/w/${workspace.id}/assistant/${created.id}${search.size ? `?${search.toString()}` : ""}`,
          { replace: true },
        );
        initialized.current = created.id;
      }
      const range = month ? monthRange(month) : { from: null, to: null };
      const body = {
        question: question.trim(),
        source_id: sourceId,
        store_ids: storeIds,
        date_from: range.from,
        date_to: range.to,
        metric_id: metricId || null,
        base_run_id: baseRun?.id ?? null,
        version: target.version,
      };
      const { version: _version, ...intent } = body;
      const signature = JSON.stringify({
        conversation_id: target.id,
        ...intent,
      });
      if (requestKey.current.body !== signature)
        requestKey.current = { body: signature, key: crypto.randomUUID() };
      const run = await post<Run>(
        workspacePath(workspace.id, `/conversations/${target.id}/messages`),
        { ...body, idempotency_key: requestKey.current.key },
      );
      return { run, conversationId: target.id, draftOrigin };
    },
    onSuccess: ({ conversationId: targetId, draftOrigin }) => {
      client.setQueryData(
        keys.resource(workspace.id, "draft", `assistant:${draftOrigin}`),
        "",
      );
      if (
        currentConversationId.current === targetId ||
        !currentConversationId.current
      ) {
        setQuestion("");
        setBaseRun(null);
      }
      requestKey.current = { body: "", key: "" };
      void client.invalidateQueries({
        queryKey: keys.resource(workspace.id, "conversation", targetId),
      });
      void client.invalidateQueries({
        queryKey: keys.resource(workspace.id, "conversations"),
      });
    },
    onError: () => {
      void client.invalidateQueries({
        queryKey: keys.resource(workspace.id, "conversation", conversationId),
      });
    },
  });
  const continueRun = (run: Run) => {
    setBaseRun(run);
    setSourceId(run.source_id);
    setStoreIds(run.store_ids);
    setMonth(run.context?.date_from?.slice(0, 7) ?? "");
    setMetricId(run.context?.metric_id ?? "");
    input.current?.focus();
    input.current?.scrollIntoView({ behavior: "smooth", block: "center" });
  };
  const newQuestion = () => {
    setBaseRun(null);
    setQuestion("");
    input.current?.focus();
  };
  const chooseSource = (value: string) => {
    setSourceId(value);
    setBaseRun(null);
    setMetricId("");
    if (conversationId) {
      navigate(`/w/${workspace.id}/assistant${historyLinkSearch}`);
      initialized.current = undefined;
    }
  };
  return (
    <>
      <PageHeader
        eyebrow="Вопрос → запрос → таблица"
        title="Ассистент"
        description="Опишите, что хотите узнать из данных. У каждого результата свои условия и SQL."
        actions={
          conversationId && conversation.data ? (
            <Button
              variant="secondary"
              onClick={() => {
                rename.reset();
                setRenameOpen(true);
              }}
            >
              Название разговора
            </Button>
          ) : undefined
        }
      />
      <Modal
        open={renameOpen}
        onOpenChange={setRenameOpen}
        title="Название разговора"
        description="Название помогает вернуться к исследованию в вашей личной истории."
      >
        <div className="modal-body">
          <Form onSubmit={(form) => rename.mutate(formText(form, "title"))}>
            <Field label="Название">
              <input
                name="title"
                required
                maxLength={160}
                defaultValue={conversation.data?.title}
              />
            </Field>
            <InlineError error={rename.error} />
            <div className="form-actions">
              <Button variant="secondary" onClick={() => setRenameOpen(false)}>
                Отмена
              </Button>
              <Button type="submit" loading={rename.isPending}>
                Сохранить название
              </Button>
            </div>
          </Form>
        </div>
      </Modal>
      <div className="assistant-layout">
        <aside
          className={`conversation-sidebar ${historyOpen ? "history-open" : ""}`}
        >
          <Button
            variant="secondary"
            icon="plus"
            className="full-width"
            onClick={() => {
              newQuestion();
              navigate(`/w/${workspace.id}/assistant${historyLinkSearch}`);
              initialized.current = undefined;
            }}
          >
            Новый разговор
          </Button>
          <Button
            className="history-toggle"
            variant="quiet"
            icon="clock"
            aria-expanded={historyOpen}
            aria-controls="conversation-history"
            onClick={() => setHistoryOpen((value) => !value)}
          >
            История
          </Button>
          <div id="conversation-history" className="conversation-history">
            <p className="eyebrow">Ваша история</p>
            <div className="search-input">
              <Icon name="search" size={16} />
              <input
                type="search"
                value={historySearch}
                onChange={(event) => {
                  const value = event.target.value;
                  setSearch(
                    (previous) => {
                      const next = new URLSearchParams(previous);
                      if (value) next.set("q", value);
                      else next.delete("q");
                      return next;
                    },
                    { replace: true },
                  );
                }}
                placeholder="Найти разговор"
                aria-label="Найти разговор"
                maxLength={200}
              />
            </div>
            {conversations.data && (
              <p className="small muted" role="status">
                Загружено: {conversationItems.length}
              </p>
            )}
            {conversations.isPending ? (
              <Loading />
            ) : conversations.error && !conversations.data ? (
              <ErrorState
                error={conversations.error}
                retry={() => void conversations.refetch()}
              />
            ) : conversationItems.length ? (
              <nav aria-label="Личные разговоры">
                {conversationItems.map((item) => (
                  <Link
                    key={item.id}
                    className={item.id === conversationId ? "active" : ""}
                    to={`/w/${workspace.id}/assistant/${item.id}${historyLinkSearch}`}
                  >
                    <span>{item.title || "Новый разговор"}</span>
                    <time dateTime={item.updated_at}>
                      {dateTime(item.updated_at)}
                    </time>
                  </Link>
                ))}
              </nav>
            ) : (
              <p className="small muted">
                {conversations.hasNextPage
                  ? "В загруженной части истории нет разговоров. Загрузите следующую страницу, чтобы проверить более ранние записи."
                  : historyQuery
                    ? "Разговоры не найдены. Измените поисковый запрос."
                    : "Здесь появятся ваши вопросы и результаты."}
              </p>
            )}
            {conversations.error && conversations.data && (
              <ErrorState
                error={conversations.error}
                retry={() =>
                  void (conversations.isFetchNextPageError
                    ? conversations.fetchNextPage()
                    : conversations.refetch())
                }
              />
            )}
            {conversations.hasNextPage && !conversations.error && (
              <Button
                variant="quiet"
                className="full-width"
                loading={conversations.isFetchingNextPage}
                disabled={conversations.isFetching}
                onClick={() => void conversations.fetchNextPage()}
              >
                Показать ещё
              </Button>
            )}
          </div>
          <div className="private-note">
            <Icon name="lock" size={15} />
            <span>
              Личная история. Коллеги получают только явно переданный результат.
            </span>
          </div>
        </aside>
        <section className="conversation-main">
          <div className="chat-context">
            <div className="chat-context-title">
              <Icon name="data" size={16} />
              <strong>Условия следующего вопроса</strong>
            </div>
            <div className="chat-context-fields">
              <label>
                Источник
                <select
                  value={sourceId}
                  onChange={(event) => chooseSource(event.target.value)}
                  disabled={Boolean(active) || mutation.isPending}
                >
                  <option value="">Выберите источник</option>
                  {sourceId && !sourceAvailable && (
                    <option value={sourceId} disabled>
                      Источник недоступен
                    </option>
                  )}
                  {availableSources.map((source) => (
                    <option key={source.id} value={source.id}>
                      {source.name}
                    </option>
                  ))}
                </select>
              </label>
              <label>
                Точки
                <select
                  value={
                    storeIds.length > 1 ? "__multiple" : (storeIds[0] ?? "")
                  }
                  onChange={(event) =>
                    setStoreIds(event.target.value ? [event.target.value] : [])
                  }
                >
                  <option value="">Все доступные</option>
                  {storeIds.length > 1 && (
                    <option value="__multiple" disabled>
                      Выбрано точек: {storeIds.length}
                    </option>
                  )}
                  {stores.data?.map((store) => (
                    <option key={store.id} value={store.id}>
                      {store.name}
                    </option>
                  ))}
                </select>
              </label>
              <label>
                Период
                <input
                  type="month"
                  value={month}
                  onChange={(event) => setMonth(event.target.value)}
                />
                <small className="context-hint">Если пусто — из вопроса</small>
              </label>
              <label>
                Определение
                <select
                  value={metricId}
                  onChange={(event) => setMetricId(event.target.value)}
                >
                  <option value="">Из вопроса</option>
                  {metrics.data
                    ?.filter((metric) => metric.source_id === sourceId)
                    .map((metric) => (
                      <option value={metric.id} key={metric.id}>
                        {metric.name}
                      </option>
                    ))}
                </select>
              </label>
            </div>
            <p>
              Эти условия относятся к новому запуску. Уже полученные таблицы не
              изменятся.
            </p>
          </div>
          <div className="conversation-feed" aria-label="Вопросы и результаты">
            {conversationId && conversation.isPending ? (
              <Loading label="Открываем историю…" />
            ) : conversation.error ? (
              <ErrorState
                error={conversation.error}
                retry={() => void conversation.refetch()}
              />
            ) : conversation.data?.runs.length ? (
              conversation.data.runs.map((run) => (
                <article className="conversation-turn" key={run.id}>
                  <div className="question-bubble">
                    <span className="question-avatar">Вы</span>
                    <div>
                      <p>{run.question}</p>
                      <span>
                        {dateTime(run.created_at)}
                        {run.context?.date_from
                          ? ` · ${periodLabel(run.context.date_from, run.context.date_to)}`
                          : ""}
                      </span>
                    </div>
                  </div>
                  <RunCard
                    initial={run}
                    onContinue={!active ? continueRun : undefined}
                  />
                </article>
              ))
            ) : (
              <div className="assistant-welcome">
                <span className="assistant-orb">
                  <Icon name="assistant" size={34} />
                </span>
                <h2>Какую таблицу вы хотите получить?</h2>
                <p>
                  Выберите источник и область. Например, можно сравнить продажи
                  точек или посмотреть динамику за период.
                </p>
                <div className="question-examples">
                  {[
                    "Покажи продажи по точкам за выбранный период",
                    "Сколько заказов было по дням?",
                    "Покажи возвраты по точкам",
                  ].map((example) => (
                    <button
                      key={example}
                      type="button"
                      onClick={() => {
                        setQuestion(example);
                        input.current?.focus();
                      }}
                    >
                      {example}
                      <Icon name="arrow" size={15} />
                    </button>
                  ))}
                </div>
                <span className="small muted">
                  Примеры — подсказки для формулировки. Доступность расчёта
                  зависит от вашего источника.
                </span>
              </div>
            )}
          </div>
          <div className="composer-wrap">
            {baseRun && (
              <div className="continuation-note">
                <Icon name="assistant" size={16} />
                <span>
                  {baseRun.status === "needs_input"
                    ? "Ответ на уточнение:"
                    : "На основе результата:"}{" "}
                  <strong>{baseRun.question}</strong>
                </span>
                <button
                  type="button"
                  className="icon-button"
                  onClick={() => setBaseRun(null)}
                  aria-label="Сбросить основание продолжения"
                >
                  <Icon name="close" size={15} />
                </button>
              </div>
            )}
            <form
              className="chat-composer"
              onSubmit={(event) => {
                event.preventDefault();
                if (
                  question.trim() &&
                  sourceAvailable &&
                  !sources.error &&
                  !conversation.error &&
                  !active &&
                  !mutation.isPending
                )
                  mutation.mutate();
              }}
            >
              <label className="sr-only" htmlFor="question-input">
                Вопрос к данным
              </label>
              <textarea
                id="question-input"
                ref={input}
                value={question}
                onChange={(event) => setQuestion(event.target.value)}
                placeholder={
                  baseRun?.status === "needs_input"
                    ? "Уточните вопрос и выберите недостающие условия выше…"
                    : "Напишите, что нужно узнать из базы данных…"
                }
                rows={3}
                maxLength={4000}
                required
                onKeyDown={(event) => {
                  if (
                    (event.ctrlKey || event.metaKey) &&
                    event.key === "Enter"
                  ) {
                    event.preventDefault();
                    event.currentTarget.form?.requestSubmit();
                  }
                }}
              />
              <div className="composer-actions">
                <span>
                  {active
                    ? "Текущий запрос ещё выполняется. Текст можно подготовить заранее."
                    : "Ctrl + Enter · SQL выполняется только для чтения"}
                </span>
                <Button
                  type="submit"
                  icon="send"
                  disabled={
                    !question.trim() ||
                    !sourceAvailable ||
                    Boolean(sources.error) ||
                    Boolean(active) ||
                    Boolean(conversation.error)
                  }
                  loading={mutation.isPending}
                >
                  Получить таблицу
                </Button>
              </div>
            </form>
            <InlineError error={mutation.error ?? sources.error} />
            {!sources.isPending && !availableSources.length && (
              <div className="warning-note">
                <Icon name="data" size={17} />
                <span>
                  Нет доступного проверенного источника.{" "}
                  {workspace.can("sources:manage") ? (
                    <Link to={`/w/${workspace.id}/data`}>
                      Настройте подключение
                    </Link>
                  ) : (
                    "Обратитесь к аналитику или администратору."
                  )}
                </span>
              </div>
            )}
            <p className="composer-note">
              Ассистент формирует запросы к данным. Объяснения сотрудников и
              управленческие выводы остаются в разборах.
            </p>
          </div>
        </section>
      </div>
    </>
  );
}
