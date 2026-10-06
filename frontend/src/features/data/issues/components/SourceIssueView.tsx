import { useEffect, useRef } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useWorkspace } from "../../../../app/workspace";
import {
  ApiError,
  get,
  post,
  workspacePath,
} from "../../../../shared/api/client";
import type {
  CaseComment,
  SourceIssueDetail,
} from "../../../../shared/api/contracts";
import { keys } from "../../../../shared/api/queries";
import {
  Button,
  ErrorState,
  Field,
  Form,
  InlineError,
  Loading,
  Panel,
  StatusBadge,
} from "../../../../shared/ui/Common";
import { dateTime, initials } from "../../../../shared/ui/format";
import { useDraft } from "../../../../shared/ui/useDraft";
import { useToast } from "../../../../shared/ui/Toast";
import { issueStatus } from "../model/status";
import { IssueResolution } from "./IssueResolution";

export function SourceIssueView({
  issueId,
  close,
}: {
  issueId: string;
  close: () => void;
}) {
  const workspace = useWorkspace();
  const client = useQueryClient();
  const notify = useToast();
  const [body, setBody] = useDraft(`source-issue-comment:${issueId}`, "");
  const queryKey = keys.resource(workspace.id, "source-issue", issueId);
  const query = useQuery({
    queryKey,
    queryFn: ({ signal }) =>
      get<SourceIssueDetail>(
        workspacePath(workspace.id, `/source-issues/${issueId}`),
        signal,
      ),
    refetchInterval: 15_000,
  });
  const refresh = () => {
    void client.invalidateQueries({ queryKey });
    void client.invalidateQueries({
      queryKey: keys.resource(workspace.id, "source-issues"),
    });
  };
  const comment = useMutation({
    mutationFn: () =>
      post<CaseComment>(
        workspacePath(workspace.id, `/source-issues/${issueId}/comments`),
        {
          body: body.trim(),
        },
      ),
    onSuccess: (created) => {
      client.setQueryData<SourceIssueDetail>(queryKey, (previous) =>
        previous
          ? {
              ...previous,
              comments: [
                ...previous.comments.filter((item) => item.id !== created.id),
                created,
              ],
            }
          : previous,
      );
      setBody("");
      notify("Ответ добавлен в обращение.");
      refresh();
    },
  });
  const detail = query.data;
  const title = useRef<HTMLHeadingElement>(null);
  useEffect(() => {
    title.current?.focus({ preventScroll: true });
  }, [detail?.id]);
  const inaccessible =
    query.error instanceof ApiError && [403, 404].includes(query.error.status);
  const sourceError =
    typeof detail?.source_error === "string"
      ? detail.source_error
      : detail?.source_error?.message;
  return (
    <>
      <Button variant="quiet" icon="back" onClick={close}>
        Все обращения
      </Button>
      {query.isPending ? (
        <Loading />
      ) : query.error && (!detail || inaccessible) ? (
        <ErrorState error={query.error} retry={() => void query.refetch()} />
      ) : (
        detail && (
          <>
            {query.error && (
              <ErrorState
                error={query.error}
                retry={() => void query.refetch()}
              />
            )}
            <div className="issue-heading">
              <div>
                <p className="eyebrow">{detail.source_name}</p>
                <h2 ref={title} tabIndex={-1}>
                  {detail.title}
                </h2>
                <p className="muted">
                  Создано{" "}
                  <time dateTime={detail.created_at}>
                    {dateTime(detail.created_at)}
                  </time>
                </p>
              </div>
              <StatusBadge status={detail.status}>
                {issueStatus[detail.status]}
              </StatusBadge>
            </div>
            <div className="two-column">
              <Panel
                title="Наблюдение и переписка"
                description="Это техническое обращение. Результаты аналитики остаются в разборах и отчётах."
              >
                <div className="issue-observation">{detail.body}</div>
                {detail.resolution && (
                  <div className="issue-resolution">
                    <strong>Что исправлено</strong>
                    <p>{detail.resolution}</p>
                  </div>
                )}
                <div className="comment-list">
                  {!detail.comments.length && (
                    <p className="issue-list-note">
                      Ответов пока нет. Здесь появятся результаты проверки и
                      изменения статуса.
                    </p>
                  )}
                  {detail.comments.map((item) => (
                    <article className="comment" key={item.id}>
                      <span className="avatar">
                        {initials(item.author_name)}
                      </span>
                      <div>
                        <strong>{item.author_name}</strong>
                        <time dateTime={item.created_at}>
                          {dateTime(item.created_at)}
                        </time>
                        <p>{item.body}</p>
                      </div>
                    </article>
                  ))}
                </div>
                <div className="discussion-composer">
                  <Form
                    onSubmit={() => {
                      if (body.trim() && !comment.isPending) comment.mutate();
                    }}
                  >
                    <Field
                      label="Ответ по обращению"
                      hint="Ctrl+Enter или ⌘+Enter — отправить ответ."
                    >
                      <textarea
                        rows={3}
                        required
                        disabled={comment.isPending}
                        maxLength={10000}
                        value={body}
                        onChange={(event) => setBody(event.target.value)}
                        onKeyDown={(event) => {
                          if (
                            (event.ctrlKey || event.metaKey) &&
                            event.key === "Enter" &&
                            body.trim() &&
                            !comment.isPending
                          ) {
                            event.preventDefault();
                            comment.mutate();
                          }
                        }}
                        placeholder="Дополните наблюдение или сообщите результат проверки"
                      />
                    </Field>
                    {body && (
                      <p className="subtle-note">
                        Черновик сохраняется при переходах внутри пространства.
                      </p>
                    )}
                    <InlineError error={comment.error} />
                    <Button
                      type="submit"
                      loading={comment.isPending}
                      disabled={!body.trim()}
                    >
                      Отправить ответ
                    </Button>
                  </Form>
                </div>
              </Panel>
              <div className="issue-context">
                <Panel
                  title="Состояние источника"
                  description="Зафиксировано при создании обращения; последующие проверки его не переписывают."
                >
                  <dl className="details-list">
                    <div>
                      <dt>При обращении</dt>
                      <dd>{sourceStatus(detail.source_status)}</dd>
                    </div>
                    <div>
                      <dt>Последняя проверка тогда</dt>
                      <dd>
                        {detail.last_checked_at
                          ? dateTime(detail.last_checked_at)
                          : "Ещё не проверялся"}
                      </dd>
                    </div>
                    <div>
                      <dt>Состояние сейчас</dt>
                      <dd>{sourceStatus(detail.current_source_status)}</dd>
                    </div>
                  </dl>
                  {sourceError && (
                    <p className="issue-source-error">{sourceError}</p>
                  )}
                </Panel>
                {workspace.can("sources:manage") && (
                  <IssueResolution detail={detail} refresh={refresh} />
                )}
              </div>
            </div>
          </>
        )
      )}
    </>
  );
}

export function sourceStatus(status: string) {
  return (
    (
      {
        ready: "Подключение проверено",
        error: "Ошибка подключения",
        new: "Ожидает проверки",
        pending: "Ожидает проверки",
        unchecked: "Ожидает проверки",
        disabled: "Отключён",
      } as Record<string, string>
    )[status] ?? status
  );
}
