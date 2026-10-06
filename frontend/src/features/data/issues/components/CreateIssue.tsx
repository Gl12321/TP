import { Link } from "react-router-dom";
import { useMutation, useQueryClient } from "@tanstack/react-query";

import { useWorkspace } from "../../../../app/workspace";
import { post, workspacePath } from "../../../../shared/api/client";
import type { SourceIssue } from "../../../../shared/api/contracts";
import { keys, useSources } from "../../../../shared/api/queries";
import {
  Button,
  EmptyState,
  ErrorState,
  Field,
  Form,
  InlineError,
  Loading,
  Modal,
} from "../../../../shared/ui/Common";
import { dateTime } from "../../../../shared/ui/format";

import { useDraft } from "../../../../shared/ui/useDraft";
import { useToast } from "../../../../shared/ui/Toast";
import { useSubmissionKey } from "../../../../shared/ui/useSubmissionKey";

import { useSourceAdministrators } from "../hooks/useSourceAdministrators";

export function CreateIssue({
  close,
  created,
}: {
  close: () => void;
  created: (id: string) => void;
}) {
  const workspace = useWorkspace();
  const client = useQueryClient();
  const notify = useToast();
  const { keyFor, clearKey } = useSubmissionKey("source-issue-create");
  const sources = useSources(workspace.id);
  const administrators = useSourceAdministrators(workspace.id);
  const empty = { source_id: "", title: "", body: "", assignee_id: "" };
  const [draft, update] = useDraft("source-issue-create", empty);
  const selectedSource = sources.data?.find(
    (source) => source.id === draft.source_id,
  );
  const validAssignee =
    !draft.assignee_id ||
    administrators.data?.some((person) => person.id === draft.assignee_id);
  const hasDraft = !!(
    draft.title ||
    draft.body ||
    draft.source_id ||
    draft.assignee_id
  );
  const mutation = useMutation({
    mutationFn: () => {
      const body = {
        ...draft,
        title: draft.title.trim(),
        body: draft.body.trim(),
        assignee_id: draft.assignee_id || null,
      };
      return post<SourceIssue>(workspacePath(workspace.id, "/source-issues"), {
        ...body,
        idempotency_key: keyFor(body),
      });
    },
    onSuccess: (issue) => {
      clearKey();
      update(empty);
      notify("Обращение отправлено администраторам источника.");
      void client.invalidateQueries({
        queryKey: keys.resource(workspace.id, "source-issues"),
      });
      created(issue.id);
    },
  });
  return (
    <Modal
      open
      onOpenChange={(value) => !value && !mutation.isPending && close()}
      title="Новое обращение по данным"
      description="Опишите технический симптом и шаги проверки. Администратор получит обращение без доступа к финансовым отчётам."
    >
      <div className="modal-body">
        {sources.isPending || administrators.isPending ? (
          <Loading />
        ) : sources.error || administrators.error ? (
          <ErrorState
            error={sources.error || administrators.error}
            retry={() => {
              void sources.refetch();
              void administrators.refetch();
            }}
          />
        ) : !sources.data?.length ? (
          <EmptyState
            icon="data"
            title="Нет доступного источника"
            description="Для обращения сначала должно появиться подключение, доступное вашей роли."
            action={
              workspace.can("sources:manage") ? (
                <Link
                  className="button secondary"
                  to={`/w/${workspace.id}/data?tab=sources`}
                  onClick={close}
                >
                  Подключить источник
                </Link>
              ) : undefined
            }
          />
        ) : (
          <Form
            onSubmit={() => {
              if (
                selectedSource &&
                validAssignee &&
                draft.title.trim() &&
                draft.body.trim() &&
                !mutation.isPending
              )
                mutation.mutate();
            }}
          >
            <Field label="Источник">
              <select
                required
                disabled={mutation.isPending}
                value={draft.source_id}
                onChange={(event) =>
                  update({ ...draft, source_id: event.target.value })
                }
              >
                <option value="">Выберите источник</option>
                {draft.source_id && !selectedSource && (
                  <option value={draft.source_id} disabled>
                    Источник больше недоступен
                  </option>
                )}
                {sources.data.map((source) => (
                  <option key={source.id} value={source.id}>
                    {source.name}
                  </option>
                ))}
              </select>
            </Field>
            {selectedSource && (
              <p className="subtle-note">
                {selectedSource.last_checked_at
                  ? `Последняя проверка: ${dateTime(selectedSource.last_checked_at)}.`
                  : "Подключение ещё не проверялось."}{" "}
                Текущее состояние сохранится в обращении.
              </p>
            )}
            <Field label="Тема обращения">
              <input
                required
                disabled={mutation.isPending}
                maxLength={200}
                value={draft.title}
                onChange={(event) =>
                  update({ ...draft, title: event.target.value })
                }
                placeholder="Например: данные не обновились за вчера"
              />
            </Field>
            <Field
              label="Что произошло"
              hint="Текст увидит техническая команда. Не вставляйте пароли и финансовые результаты."
            >
              <textarea
                required
                disabled={mutation.isPending}
                rows={5}
                maxLength={10000}
                value={draft.body}
                onChange={(event) =>
                  update({ ...draft, body: event.target.value })
                }
                placeholder="Какой период проверяли, когда заметили проблему, что ожидали увидеть"
              />
            </Field>
            <Field label="Ответственный">
              <select
                disabled={mutation.isPending}
                value={draft.assignee_id}
                onChange={(event) =>
                  update({ ...draft, assignee_id: event.target.value })
                }
              >
                <option value="">
                  Сообщить всем администраторам источников
                </option>
                {!validAssignee && (
                  <option value={draft.assignee_id} disabled>
                    Участник больше недоступен
                  </option>
                )}
                {administrators.data?.map((person) => (
                  <option key={person.id} value={person.id}>
                    {person.name}
                  </option>
                ))}
              </select>
            </Field>
            {hasDraft && (
              <div className="issue-draft-note">
                <span>
                  Черновик сохраняется при переходах внутри пространства.
                </span>
                <Button
                  variant="quiet"
                  disabled={mutation.isPending}
                  onClick={() => update(empty)}
                >
                  Очистить
                </Button>
              </div>
            )}
            <InlineError error={mutation.error} />
            <div className="form-actions">
              <Button
                variant="secondary"
                onClick={close}
                disabled={mutation.isPending}
              >
                Отмена
              </Button>
              <Button
                type="submit"
                loading={mutation.isPending}
                disabled={
                  !selectedSource ||
                  !validAssignee ||
                  !draft.title.trim() ||
                  !draft.body.trim()
                }
              >
                Отправить обращение
              </Button>
            </div>
          </Form>
        )}
      </div>
    </Modal>
  );
}
