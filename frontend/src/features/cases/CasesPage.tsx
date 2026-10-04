import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useSession } from "../../app/session";
import { useWorkspace } from "../../app/workspace";
import { get, post, queryString, workspacePath } from "../../shared/api/client";
import type {
  Case,
  CaseDetail,
  Participant,
  Run,
} from "../../shared/api/contracts";
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
  PageHeader,
  Panel,
  StatusBadge,
} from "../../shared/ui/Common";
import { dateTime, initials } from "../../shared/ui/format";
import { Icon } from "../../shared/ui/Icon";
import { ResultPanel } from "../../shared/results/ResultPanel";
import { CaseCreateDialog } from "./CaseCreateDialog";
import { caseStatus } from "../stores/StoresPage";
import { Measurements } from "./Measurements";
import { useDraft } from "../../shared/ui/useDraft";

export function CasesPage() {
  const workspace = useWorkspace();
  const { session } = useSession();
  const { caseId } = useParams();
  const [create, setCreate] = useState(false);
  const [filter, setFilter] = useState("all");
  const query = useQuery({
    queryKey: keys.resource(workspace.id, "cases"),
    queryFn: ({ signal }) =>
      get<Case[]>(workspacePath(workspace.id, "/cases"), signal),
  });
  if (caseId) return <CaseView caseId={caseId} />;
  const items =
    query.data?.filter(
      (item) =>
        filter === "all" ||
        (filter === "mine"
          ? item.created_by === session?.user.id
          : filter === "waiting"
            ? item.pending_for_me
            : filter === "closed"
              ? ["closed", "done"].includes(item.status)
              : !["closed", "done"].includes(item.status)),
    ) ?? [];
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
        <div className="list-toolbar">
          <div className="segmented" aria-label="Фильтр разборов">
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
                onClick={() => setFilter(value)}
              >
                {label}
              </button>
            ))}
          </div>
          <span className="list-count">{items.length} разборов</span>
        </div>
        {query.isPending ? (
          <Loading />
        ) : query.error ? (
          <ErrorState error={query.error} retry={() => void query.refetch()} />
        ) : items.length ? (
          <div className="object-list">
            {items.map((item) => (
              <Link
                className="object-row"
                key={item.id}
                to={`/w/${workspace.id}/cases/${item.id}`}
              >
                <span className="object-symbol">
                  <Icon name="cases" />
                </span>
                <div>
                  <strong>{item.title}</strong>
                  <span>
                    {item.description || "Без дополнительного описания"}
                  </span>
                  <time>{dateTime(item.updated_at)}</time>
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
            title="Здесь появятся ваши разборы"
            description="Создайте обсуждение по точке или сохраните конкретный результат из ассистента. Вопрос сотруднику будет связан с его основанием."
          />
        )}
      </Panel>
      {create && <CaseCreateDialog close={() => setCreate(false)} />}
    </>
  );
}

function CaseView({ caseId }: { caseId: string }) {
  const workspace = useWorkspace();
  const { session } = useSession();
  const client = useQueryClient();
  const stores = useStores(workspace.id);
  const query = useQuery({
    queryKey: keys.resource(workspace.id, "case", caseId),
    queryFn: ({ signal }) =>
      get<CaseDetail>(workspacePath(workspace.id, `/cases/${caseId}`), signal),
    refetchInterval: 15_000,
  });
  const detail = query.data;
  const participants = useQuery({
    staleTime: 0,
    refetchOnMount: "always",
    queryKey: keys.resource(workspace.id, "participants", detail?.store_ids),
    queryFn: ({ signal }) =>
      get<Participant[]>(
        workspacePath(
          workspace.id,
          `/participants${queryString({ store_ids: detail?.store_ids })}`,
        ),
        signal,
      ),
    enabled: Boolean(detail) && workspace.can("cases:write"),
  });
  const run = useQuery({
    queryKey: keys.resource(workspace.id, "case-run", caseId, detail?.run_id),
    queryFn: ({ signal }) =>
      get<Run>(workspacePath(workspace.id, `/runs/${detail?.run_id}`), signal),
    enabled: Boolean(detail?.run_id),
  });
  const [mode, setMode] = useState<"comment" | "question" | "conclusion">(
    "comment",
  );
  const [body, setBody] = useDraft(`case:${caseId}:${mode}`, "");
  const [assignee, setAssignee] = useState("");
  const mutation = useMutation({
    mutationFn: ({ path, payload }: { path: string; payload: object }) =>
      post(workspacePath(workspace.id, `/cases/${caseId}${path}`), payload),
    onSuccess: () => {
      setBody("");
      void client.invalidateQueries({
        queryKey: keys.resource(workspace.id, "case", caseId),
      });
      void client.invalidateQueries({
        queryKey: keys.resource(workspace.id, "cases"),
      });
    },
  });
  if (query.isPending) return <Loading />;
  if (query.error)
    return (
      <ErrorState error={query.error} retry={() => void query.refetch()} />
    );
  if (!detail) return null;
  const own = detail.created_by === session?.user.id;
  const closed = ["closed", "done"].includes(detail.status);
  const canWrite = workspace.can("cases:write") && !closed;
  const scope = detail.store_ids
    .map((id) => stores.data?.find((store) => store.id === id)?.name ?? "Точка")
    .join(", ");
  return (
    <>
      <Link className="back-link" to={`/w/${workspace.id}/cases`}>
        <Icon name="back" size={16} />
        Все разборы
      </Link>
      <PageHeader
        eyebrow={scope || "Выбранная область"}
        title={detail.title}
        description={`Создан ${dateTime(detail.created_at)}`}
        actions={
          <StatusBadge status={detail.status}>
            {caseStatus(detail.status)}
          </StatusBadge>
        }
      />
      <div className="case-layout">
        <div>
          <Panel title="Основание разбора">
            <div className="panel-body">
              <p className="preserve-lines">
                {detail.description || "Дополнительное описание не указано."}
              </p>
              {detail.run_id ? (
                <div className="case-result">
                  {run.isPending ? (
                    <Loading />
                  ) : run.error ? (
                    <ErrorState error={run.error} />
                  ) : (
                    run.data && <ResultPanel run={run.data} compact />
                  )}
                </div>
              ) : (
                !detail.measurements?.length && (
                  <p className="subtle-note">
                    Результат расчёта не прикреплён.
                  </p>
                )
              )}
            </div>
          </Panel>
          {Boolean(detail.measurements?.length) && (
            <Measurements
              items={detail.measurements!}
              caseId={caseId}
              canMeasure={own && workspace.can("cases:write")}
            />
          )}
          <Panel
            title="Обсуждение"
            description="Вопросы ответственным, ответы команды и итог."
            className="spaced"
          >
            {!detail.comments.length && !detail.questions.length ? (
              <div className="panel-body">
                <p className="muted">
                  Обсуждение ещё не началось. Можно добавить контекст или задать
                  адресный вопрос.
                </p>
              </div>
            ) : (
              <div className="discussion-thread">
                {detail.comments.map((comment) => (
                  <article className="comment" key={comment.id}>
                    <span className="avatar light">
                      {initials(comment.author_name)}
                    </span>
                    <div>
                      <header>
                        <strong>{comment.author_name}</strong>
                        <time>{dateTime(comment.created_at)}</time>
                      </header>
                      <p>{comment.body}</p>
                    </div>
                  </article>
                ))}
                {detail.questions.map((question) => (
                  <article className="addressed-question" key={question.id}>
                    <div className="addressed-heading">
                      <Icon name="send" size={16} />
                      <strong>
                        Вопрос:{" "}
                        {participants.data?.find(
                          (item) => item.id === question.assignee_id,
                        )?.name ??
                          (question.assignee_id === session?.user.id
                            ? "вам"
                            : "ответственному участнику")}
                      </strong>
                      <StatusBadge
                        status={question.answer ? "answered" : "waiting"}
                      >
                        {question.answer ? "Есть ответ" : "Ждём ответа"}
                      </StatusBadge>
                    </div>
                    <p className="preserve-lines">{question.body}</p>
                    {question.answer ? (
                      <div className="answer">
                        <span>Ответ сотрудника</span>
                        <p>{question.answer}</p>
                      </div>
                    ) : question.assignee_id === session?.user.id && !closed ? (
                      <Form
                        onSubmit={(form) =>
                          mutation.mutate({
                            path: `/questions/${question.id}/answer`,
                            payload: { answer: formText(form, "answer") },
                          })
                        }
                      >
                        <Field label="Ваш ответ">
                          <textarea
                            name="answer"
                            required
                            rows={3}
                            maxLength={4000}
                            placeholder="Что удалось проверить? Укажите факты и источник сведений."
                          />
                        </Field>
                        <Button type="submit" loading={mutation.isPending}>
                          Отправить ответ
                        </Button>
                      </Form>
                    ) : null}
                  </article>
                ))}
              </div>
            )}
            {detail.conclusion && (
              <div className="conclusion">
                <span>
                  <Icon name="check" size={18} />
                  Итог разбора
                </span>
                <p>{detail.conclusion}</p>
              </div>
            )}
            {canWrite && (
              <div className="discussion-composer">
                <div className="segmented">
                  <button
                    type="button"
                    aria-pressed={mode === "comment"}
                    className={mode === "comment" ? "active" : ""}
                    onClick={() => setMode("comment")}
                  >
                    Комментарий
                  </button>
                  {own && (
                    <>
                      <button
                        type="button"
                        aria-pressed={mode === "question"}
                        className={mode === "question" ? "active" : ""}
                        onClick={() => setMode("question")}
                      >
                        Задать вопрос
                      </button>
                      <button
                        type="button"
                        aria-pressed={mode === "conclusion"}
                        className={mode === "conclusion" ? "active" : ""}
                        onClick={() => setMode("conclusion")}
                      >
                        Итог
                      </button>
                    </>
                  )}
                </div>
                <form
                  className="form"
                  onSubmit={(event) => {
                    event.preventDefault();
                    mutation.mutate({
                      path:
                        mode === "question"
                          ? "/questions"
                          : mode === "conclusion"
                            ? "/close"
                            : "/comments",
                      payload:
                        mode === "question"
                          ? { body: body.trim(), assignee_id: assignee }
                          : mode === "conclusion"
                            ? { conclusion: body.trim() }
                            : { body: body.trim() },
                    });
                  }}
                >
                  {mode === "question" && (
                    <Field
                      label="Получатель"
                      hint="Показаны участники с доступом ко всей области этого разбора."
                    >
                      <select
                        value={assignee}
                        onChange={(event) => setAssignee(event.target.value)}
                        required
                      >
                        <option value="">Выберите участника</option>
                        {participants.data
                          ?.filter((item) => item.id !== session?.user.id)
                          .map((item) => (
                            <option value={item.id} key={item.id}>
                              {item.name}
                            </option>
                          ))}
                      </select>
                    </Field>
                  )}
                  <Field
                    label={
                      mode === "question"
                        ? "Предметный вопрос"
                        : mode === "conclusion"
                          ? "Что установили"
                          : "Сообщение"
                    }
                  >
                    <textarea
                      value={body}
                      onChange={(event) => setBody(event.target.value)}
                      rows={4}
                      required
                      maxLength={4000}
                      placeholder={
                        mode === "conclusion"
                          ? "Зафиксируйте подтверждённые обстоятельства и оставшиеся ограничения."
                          : "Опишите факты, которые помогут разобраться."
                      }
                    />
                  </Field>
                  {mode === "question" && assignee && body.trim() && (
                    <div className="info-note">
                      <Icon name="send" size={16} />
                      <span>
                        Получатель увидит этот разбор, прикреплённый результат и
                        ваш вопрос. Личный диалог ассистента не передаётся.
                      </span>
                    </div>
                  )}
                  <Button
                    type="submit"
                    loading={mutation.isPending}
                    disabled={
                      !body.trim() || (mode === "question" && !assignee)
                    }
                  >
                    {mode === "conclusion"
                      ? "Зафиксировать итог"
                      : mode === "question"
                        ? "Задать вопрос"
                        : "Добавить комментарий"}
                  </Button>
                </form>
              </div>
            )}
            <div className="panel-body">
              <InlineError error={mutation.error ?? participants.error} />
            </div>
          </Panel>
        </div>
        <aside>
          <Panel title="Контекст">
            <dl className="details-list">
              <div>
                <dt>Область</dt>
                <dd>{scope}</dd>
              </div>
              <div>
                <dt>Основание</dt>
                <dd>
                  {detail.run_id
                    ? "Сохранённый результат"
                    : detail.measurements?.length
                      ? "Снимок показателя"
                      : "Описание автора"}
                </dd>
              </div>
              <div>
                <dt>Состояние</dt>
                <dd>{caseStatus(detail.status)}</dd>
              </div>
            </dl>
            <div className="panel-body">
              <p className="small muted">
                Уточняйте обстоятельства у ответственного за точку. Автор
                разбора фиксирует итог после обсуждения.
              </p>
            </div>
          </Panel>
        </aside>
      </div>
    </>
  );
}
